"""Regression checks for the medication graph. Read-only: it never changes the database.

Run after any change to the data build, the queries or the curated lists:
    .venv/bin/python tests/run_regression.py
Prints PASS/FAIL per check and exits with code 1 if anything failed.
Needs Neo4j running (docker start medicine-neo4j) and the database loaded.
"""
import csv
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import queries  # noqa: E402
from config import get_driver  # noqa: E402

BUILD = ROOT / "build"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def read(name):
    with open(BUILD / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main():
    d = get_driver()
    d.verify_connectivity()
    analyse = lambda meds, conds=(), new=None: queries.analyse(d, list(meds), list(conds), new)
    labels = lambda r: [m["label"] for m in r["medicines"]]
    kinds = lambda r, kind: [f["title"] for f in r["findings"] if f["kind"] == kind]

    print("--- data build (build/*.csv)")
    drugs = {r["key"]: r for r in read("drugs.csv")}
    brands = {r["key"]: r for r in read("brands.csv")}
    contains = defaultdict(list)
    for r in read("contains.csv"):
        contains[r["brand"]].append(drugs[r["drug"]]["name"])
    check("Jan Aushadhi insulin glargine is not insulin human", contains["JA:363"] == ["insulin glargine"], contains["JA:363"])
    check("Jan Aushadhi metformin + glimepiride keeps both", sorted(contains["JA:34"]) == ["glimepiride", "metformin"], contains["JA:34"])
    check("Jan Aushadhi products skipped stays low", len(read("ja_skipped.csv")) <= 130, len(read("ja_skipped.csv")))
    merged = [set(r["raw_names"].split(" | ")) for r in read("merge_log.csv")]
    wrong = [("ardeparin", "bemiparin"), ("tinzaparin", "bemiparin"), ("urofollitropin", "follitropin"),
             ("norgestrel", "levonorgestrel")]
    check("blocked wrong merges stay blocked", not any(a in m and b in m for m in merged for a, b in wrong))
    classes = {drugs[k]["name"]: drugs[k]["atc_classes"] for k in drugs}
    check("combination drug classes excluded (amlodipine only C08CA)", classes.get("amlodipine") == "C08CA", classes.get("amlodipine"))
    flagged = sum(1 for b in brands.values() if b["composition_incomplete"] == "True")
    check("incomplete compositions flagged", 5000 < flagged < 8000, flagged)

    print("--- typing medicines")
    rows = [b for b in brands.values() if b["source"] == "india_az" and b["discontinued"] == "False"]
    random.seed(7)
    form = re.compile(r"\s+(tablets?|capsules?|syrup|injection|suspension|cream|gel|ointment|drops|solution|oral|dt|sr|er|"
                      r"xr|pr|mr|cr|lotion|spray|powder|infusion|inhaler|respules|eye|ear|nasal)\b.*$", re.I)
    exact = 0
    for b in random.sample(rows, 400):
        typed = form.sub("", b["name"]).strip() or b["name"]
        r = queries.resolve_medicine(d, typed)
        exact += bool(r) and form.sub("", r["label"]).strip().lower() == typed.lower()
    check("400 random Indian brands typed without the form word resolve exactly (>= 390)", exact >= 390, f"{exact}/400")
    for typed, want in [("Pan 40", "PAN 40 Tablet"), ("Telma 40", "Telma 40 Tablet"), ("Zerodol-P", "Zerodol-P Tablet"),
                        ("Montair-LC", "Montair-LC Tablet"), ("acetaminophen", "paracetamol"), ("albuterol", "salbutamol"),
                        ("glyburide", "glibenclamide"), ("vitamin d3", "colecalciferol"), ("olmesartan", "olmesartan medoxomil"),
                        ("Tacil D", "Tacil D 10 mg/325 mg/37.5 mg Tablet")]:
        r = queries.resolve_medicine(d, typed)
        check(f"'{typed}' -> {want}", bool(r) and r["label"] == want, r and r["label"])
    check("Tacil D is flagged as composition incomplete", queries.resolve_medicine(d, "Tacil D")["incomplete"])

    print("--- typing conditions")
    for typed, want in [("kidney", "Kidney disease"), ("sugar", "Diabetes"), ("BP", "High blood pressure"),
                        ("hypertension", "High blood pressure"), ("fits", "Epilepsy / seizures"), ("TB", "Tuberculosis")]:
        c = queries.resolve_condition(d, typed)
        check(f"condition '{typed}' -> {want}", bool(c) and c["name"] == want, c and c["name"])
    check("nonsense condition is not guessed", queries.resolve_condition(d, "xyzzy") is None)

    print("--- did you mean")
    for typed, kind, want in [("paracetmol", "medicine", "paracetamol"), ("crocn advance", "medicine", "Crocin Advance Tablet"),
                              ("dolo 65O", "medicine", "Dolo 650 Tablet"), ("diabetis", "condition", "Diabetes"),
                              ("athsma", "condition", "Asthma")]:
        s = queries.did_you_mean(d, typed, kind)
        check(f"'{typed}' suggests {want}", want in s, s)
    r = analyse(["paracetmol", "warfarin"], ["kidny"])
    check("unrecognised inputs come back with suggestions",
          [u["field"] for u in r["unknown"]] == ["medicines", "conditions"] and all(u["suggestions"] for u in r["unknown"]))

    print("--- findings")
    for meds in [["Zomesar 20mg", "lisinopril"], ["Altipod 200mg", "bumetanide"], ["Winpace 5", "phenytoin"],
                 ["Wosulin 30/70", "levofloxacin"], ["Zytiprost", "tramadol"], ["Cresemba", "tacrolimus"]]:
        r = analyse(meds)
        check(f"{' + '.join(meds)} finds an interaction", r["score"] > 0, r["score"])
    r = analyse(["warfarin", "Ecosprin 75"])
    check("warfarin + aspirin is Major", any("Major" in t for t in kinds(r, "Drug interaction")))
    r = analyse(["Dolo 650", "Crocin Advance"])
    check("paracetamol in Dolo 650 + Crocin is a duplicate", len(kinds(r, "Duplicate ingredient")) == 1)
    r = analyse(["rifampicin", "tizanidine"])
    check("rifampicin -> CYP1A2 -> tizanidine hidden interaction", any("CYP1A2" in t for t in kinds(r, "Hidden enzyme interaction")))
    r = analyse(["Combiflam", "Glycomet 500", "Telma 40"], ["kidney"])
    unsafe = " ".join(kinds(r, "Unsafe for patient's condition"))
    check("kidney disease flags metformin, ibuprofen and telmisartan",
          all(x in unsafe for x in ("metformin", "ibuprofen", "telmisartan")), unsafe)
    r = analyse(["amlodipine", "hydrochlorothiazide", "enalapril", "Dolo 650", "Ecosprin 75", "metformin"])
    check("no false 'same drug class' warnings", not kinds(r, "Same drug class"), kinds(r, "Same drug class"))
    r = analyse(["Brufen 400", "warfarin"], new="ibuprofen")
    check("what-if lists only new risks (adding ibuprofen to Brufen = duplicate only)",
          [f["kind"] for f in r["what_if"]["new"]] == ["Duplicate ingredient"], [f["title"] for f in r["what_if"]["new"]])

    print("--- prescribing cascades")
    r = analyse(["amlodipine", "furosemide", "ibuprofen", "pantoprazole", "enalapril", "dextromethorphan",
                 "hydrochlorothiazide", "allopurinol", "lithium", "levothyroxine"])
    text = " | ".join(c["title"] for c in r["cascades"])
    for want in ["furosemide may only be needed because amlodipine can cause ankle swelling",
                 "pantoprazole may only be needed because ibuprofen",
                 "dextromethorphan may only be needed because enalapril can cause dry cough",
                 "allopurinol may only be needed because hydrochlorothiazide"]:
        check(f"cascade present: {want[:60]}", want in text)
    check("no false cascade: amlodipine does not cause dry cough", "amlodipine can cause dry cough" not in text)
    check("cascades capped at 10", len(r["cascades"]) <= 10, len(r["cascades"]))

    print("--- safer alternatives and generics")
    key = lambda n: d.execute_query("MATCH (x:Drug {name_lower: $n}) RETURN x.key AS k", n=n).records[0]["k"]
    alts = queries._alternatives(d, key("glibenclamide"), [key("glibenclamide")], [], 4)
    generics = [a["generic"] for a in alts if a["generic"]]
    check("alternatives offered for glibenclamide", len(alts) >= 3, [a["name"] for a in alts])
    check("no combination product offered as a generic",
          not any(re.search(r"(?i)( and |\+|,| with |combi)", g["name"]) for g in generics), [g["name"] for g in generics])
    alts = queries._alternatives(d, key("losartan"), [key("losartan")], [], 3)
    check("alternatives match through condition groups (losartan -> other ARBs)",
          any(a["name"] in ("telmisartan", "olmesartan medoxomil", "valsartan", "irbesartan", "azilsartan medoxomil") for a in alts),
          [a["name"] for a in alts])

    print("--- data coverage notices")
    r = analyse(["Zerodol-SP", "warfarin"], ["kidney"])
    check("drugs with no interaction data are reported, not silently scored 0",
          any("No interaction data for aceclofenac" in n for n in r["coverage"]), r["coverage"])
    print("--- risk level")
    level = lambda *w: queries.risk_level([{"weight": x} for x in w])["level"]
    check("one serious finding is Critical however small the total", level(3) == "Critical", level(3))
    check("many minor findings stay Low however large the total", level(*[1] * 12) == "Low", level(*[1] * 12))
    check("levels step Moderate -> High -> None found",
          (level(2, 1), level(2, 2), level()) == ("Moderate", "High", "None found"), (level(2, 1), level(2, 2), level()))
    r = analyse(["clozapine", "ozanimod", "citalopram"])
    check("three major interactions give Critical", r["level"]["level"] == "Critical" and r["score"] == 9, (r["level"], r["score"]))
    print("--- plain-language explanations")
    names = sorted({n for g in queries.EFFECT_GROUPS for n in g["names"]})
    missing = [r["n"] for r in d.execute_query(
        "UNWIND $n AS n WITH n WHERE NOT EXISTS { MATCH (:Drug {name_lower: n}) } RETURN n", n=names).records]
    check("every drug named in EFFECT_GROUPS exists in the graph", not missing, missing)
    titles = lambda f: [g["title"] for g in f["effects"]]
    r = analyse(["clozapine", "ozanimod", "citalopram"])
    check("every finding says what it means", all(f["meaning"] for f in r["findings"] + r["cascades"]))
    check("QT drugs explained as a heart rhythm risk", all("Heart rhythm" in titles(f) for f in r["findings"]),
          [titles(f) for f in r["findings"]])
    check("three heart-rhythm drugs flagged as adding up", [s["group"]["id"] for s in r["stacked"]] == ["heart_rhythm"],
          r["stacked"])
    alcohol = {a["drug"]: [g["id"] for g in a["effects"]] for a in r["alcohol"]}
    check("clozapine + alcohol explained as drowsiness", "drowsiness" in alcohol.get("clozapine", []), alcohol)
    r = analyse(["Brufen 400", "Dolo 650"])
    check("no made-up reason for ibuprofen + paracetamol (noisy side effects ignored)",
          all(not f["effects"] for f in r["findings"]), [titles(f) for f in r["findings"]])
    alcohol = {a["drug"]: [g["id"] for g in a["effects"]] for a in r["alcohol"]}
    check("paracetamol + alcohol explained as liver strain only", alcohol.get("paracetamol") == ["liver"], alcohol)
    r = analyse(["Brufen 400", "Telma 40", "Lasix"])
    check("painkiller + BP medicine + water pill flagged as kidney strain adding up",
          "kidney" in [s["group"]["id"] for s in r["stacked"]], r["stacked"])
    r = analyse(["amlodipine", "telmisartan", "hydrochlorothiazide"])
    check("three blood pressure medicines are not a low-BP warning (normal treatment)",
          "low_bp" not in [s["group"]["id"] for s in r["stacked"]], r["stacked"])
    top = queries.top_risk_medicines(d, 5)
    check("GDS risk ranking is populated", len(top) == 5 and top[0]["risk_score"] > 1000, top[:2])
    d.close()

    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
