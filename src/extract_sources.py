"""Cuts the big raw downloads down to small files the data build reads (run once after downloading):

    python src/extract_sources.py

openFDA drug labels (https://open.fda.gov/apis/drug/label/, public domain), 14 zips in data/fda/raw/
    -> data/fda/labels.jsonl.gz: one label per drug, the sections a patient needs.
TWOSIDES (https://tatonettilab.org/offsides/, side effects reported for drug pairs in the FDA adverse event
    reports), TWOSIDES.csv.gz in data/twosides/raw/  -> data/twosides/twosides_pairs.csv.gz: strong signals only.

250k Indian medicines (Kaggle, shudhanshusingh/250k-medicines-usage-side-effects-and-substitutes, CC BY-SA 4.0),
    medicine_dataset.csv in data/india/raw/  -> data/india/medicine_details.csv.gz: uses, side effects,
    substitutes, habit forming and classes per brand, the repeated columns joined into lists.

The first two keep only drugs the resolver recognises, so a rebuild never needs the 2.7 GB of raw files.
"""
import csv
import glob
import gzip
import heapq
import json
import re
import sys
import zipfile
from collections import defaultdict

from curated import REPORTED_EFFECTS
from prepare_data import DATA, DrugResolver

# label sections kept, in the order they are shown
SECTIONS = ["boxed_warning", "indications_and_usage", "contraindications", "warnings_and_cautions", "warnings",
            "drug_interactions", "information_for_patients", "geriatric_use", "pregnancy"]
MAX_SECTION = 40_000          # characters; a few labels repeat whole tables
# TWOSIDES: a side effect counts for a pair when at least this many reports name it, it is reported this many
# times more often with the pair than with matched control drugs (PRR), and it is a serious, recognisable event
# (curated.REPORTED_EFFECTS); the most-reported few per pair are kept
MIN_REPORTS, MIN_PRR, PER_PAIR = 10, 3.0, 8
SERIOUS = re.compile(REPORTED_EFFECTS)


def labels(res):
    best = {}                  # drug key -> (rank, label)
    for path in sorted(glob.glob(str(DATA / "fda/raw/drug-label-*.json.zip"))):
        with zipfile.ZipFile(path) as z:
            results = json.load(z.open(z.namelist()[0]))["results"]
        for lab in results:
            fda = lab.get("openfda", {})
            substances = fda.get("substance_name", [])
            if len(substances) != 1 or not (res.dc_id(substances[0])):
                continue       # combination labels would put one drug's warnings on another
            key = res.key(substances[0])
            rx = "HUMAN PRESCRIPTION DRUG" in fda.get("product_type", [])
            # a label with an interactions section first, then prescription over OTC, then the newest
            rank = ("drug_interactions" in lab, "boxed_warning" in lab, rx, lab.get("effective_time", ""))
            if key not in best or rank > best[key][0]:
                best[key] = (rank, {
                    "drug": key, "substance": substances[0], "set_id": lab.get("set_id", ""),
                    "effective": lab.get("effective_time", ""), "brand": (fda.get("brand_name") or [""])[0],
                    "sections": {s: " ".join(lab[s])[:MAX_SECTION] for s in SECTIONS if lab.get(s)}})
        print(f"  {path.rsplit('/', 1)[-1]}: {len(best):,} drugs with a label so far", flush=True)
    out = DATA / "fda/labels.jsonl.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for _, lab in sorted(best.values(), key=lambda v: v[1]["drug"]):
            f.write(json.dumps(lab, ensure_ascii=False) + "\n")
    print(f"{len(best):,} drug labels -> {out}")


def twosides(res):
    keep = defaultdict(list)   # (key a, key b) -> heap of (reports, prr, effect)
    rows = 0
    with gzip.open(DATA / "twosides/raw/TWOSIDES.csv.gz", "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows += 1
            if r["A"] == "A":
                continue       # the file repeats its header part-way through
            reports, prr = int(r["A"]), float(r["PRR"])
            if reports < MIN_REPORTS or prr < MIN_PRR or not SERIOUS.search(r["condition_concept_name"]):
                continue
            a, b = res.dc_id(r["drug_1_concept_name"]), res.dc_id(r["drug_2_concept_name"])
            if not a or not b or a == b:
                continue
            pair = tuple(sorted((f"DC:{a}", f"DC:{b}")))
            item = (reports, prr, r["condition_concept_name"])
            heap = keep[pair]
            (heapq.heappush if len(heap) < PER_PAIR else heapq.heappushpop)(heap, item)
            if rows % 5_000_000 == 0:
                print(f"  {rows:,} rows read, {len(keep):,} pairs", flush=True)
    out = DATA / "twosides/twosides_pairs.csv.gz"
    with gzip.open(out, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["a", "b", "effect", "reports", "prr"])
        for (a, b), heap in sorted(keep.items()):
            for reports, prr, effect in sorted(heap, reverse=True):
                w.writerow([a, b, effect, reports, round(prr, 2)])
    print(f"{rows:,} rows read, {len(keep):,} drug pairs kept -> {out}")


def india(res):
    out = DATA / "india/medicine_details.csv.gz"
    listed = lambda r, prefix: "|".join(v.strip() for k, v in r.items() if k.startswith(prefix) and v.strip())
    blank = lambda v: "" if v.strip() in ("", "NA") else v.strip()
    n = 0
    with open(DATA / "india/raw/medicine_dataset.csv", encoding="utf-8", newline="") as f, \
            gzip.open(out, "wt", encoding="utf-8", newline="") as g:
        w = csv.writer(g)
        w.writerow(["name", "uses", "side_effects", "substitutes", "habit_forming", "therapeutic_class",
                    "action_class", "chemical_class"])
        for r in csv.DictReader(f):
            w.writerow([r["name"].strip(), listed(r, "use"), listed(r, "sideEffect"), listed(r, "substitute"),
                        r["Habit Forming"].strip() == "Yes", blank(r["Therapeutic Class"]), blank(r["Action Class"]),
                        blank(r["Chemical Class"])])
            n += 1
    print(f"{n:,} Indian medicines -> {out}")


if __name__ == "__main__":
    which = sys.argv[1:] or ["labels", "twosides", "india"]
    resolver = DrugResolver()
    for name in which:
        print(f"Extracting {name} ...")
        {"labels": labels, "twosides": twosides, "india": india}[name](resolver)
