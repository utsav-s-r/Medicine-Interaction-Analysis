"""Turn the raw downloads in data/ into clean node and relationship CSVs in build/.

Every medicine is identified by one key, so the same drug coming from different
sources ends up as a single Drug node:
  DC:<id>   the drug was found in DrugCentral (by its name or one of its synonyms)
  N:<name>  the drug is not in DrugCentral, so it is keyed by its cleaned name

Run:  python src/prepare_data.py
"""
import csv
import difflib
import glob
import gzip
import itertools
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from curated import ATC_GROUPS, LABEL_CLASS_TERMS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"
csv.field_size_limit(sys.maxsize)

SEVERITY_RANK = {"Unknown": 0, "Minor": 1, "Moderate": 2, "Major": 3}

# Salt, ester and form words that name the same medicine ("metoprolol tartrate" is metoprolol,
# "olmesartan medoxomil" is olmesartan). "acetate" and "carbonate" are not stripped: they change the drug.
SALT_WORDS = {
    "hydrochloride", "hcl", "dihydrochloride", "sodium", "disodium", "potassium", "calcium", "magnesium",
    "maleate", "sulphate", "sulfate", "citrate", "tartrate", "succinate", "besylate", "besilate",
    "mesylate", "mesilate", "phosphate", "bromide", "fumarate", "hyclate", "propionate",
    "dipropionate", "valerate", "furoate", "monohydrate", "dihydrate", "trihydrate", "anhydrous",
    "medoxomil", "proxetil", "axetil", "cilexetil", "pivoxil", "dipivoxil", "disoproxil", "decanoate",
    "acetonide", "caproate", "etabonate", "butylbromide",
    "ip", "bp", "usp",
}
LEADING_SALTS = {"potassium", "calcium", "sodium", "magnesium"}
# Stripping must not leave a meaningless stem ("ferrous fumarate" is not "ferrous").
STEM_JUNK = {"ferrous", "ferric", "monomethyl", "sodium", "potassium", "calcium", "magnesium", "zinc"}

# Other names for a DrugCentral drug that its synonym list does not cover (Indian, British, misspellings).
ALIASES = {
    "tazobactum": "tazobactam", "adrenaline": "epinephrine", "noradrenaline": "norepinephrine",
    "s-amlodipine": "levamlodipine", "s amlodipine": "levamlodipine", "s-metoprolol": "metoprolol",
    "s-etodolac": "etodolac", "human insulin": "insulin human", "soluble insulin": "insulin human",
    "insulin isophane": "insulin human", "insulin isophane/nph": "insulin human", "nph insulin": "insulin human",
    "levo-carnitine": "levocarnitine", "potassium clavulanate": "clavulanic acid", "clavulanate": "clavulanic acid",
    "calcium leucovorin": "leucovorin", "folinic acid": "leucovorin", "beclomethasone": "beclometasone dipropionate",
    "beclometasone": "beclometasone dipropionate", "isavuconazole": "isavuconazonium", "abiraterone": "abiraterone acetate",
    "tenofovir disoproxil fumarate": "tenofovir disoproxil", "l-dopa": "levodopa", "lithium": "lithium carbonate",
    "trioxasalen": "trioxsalen", "clinidipine": "cilnidipine", "cetrizine": "cetirizine", "levo-thyroxine": "levothyroxine",
    "glimipride": "glimepiride", "flecanide": "flecainide", "duloxetin": "duloxetine", "paroxetin": "paroxetine",
    "vildaglipitin": "vildagliptin", "fluromethalone": "fluorometholone", "trihexiphenidyl": "trihexyphenidyl",
    "vitamin d3": "colecalciferol", "vitamin d": "colecalciferol", "cholecalciferol": "colecalciferol",
    "vitamin b12": "cyanocobalamin", "methylcobalamin": "mecobalamin", "vitamin c": "ascorbic acid",
    "lignocaine": "lidocaine", "frusemide": "furosemide", "aspirin": "acetylsalicylic acid",
    "l-methyl folate": "levomefolic acid", "recombinant human erythropoietin alfa": "epoetin alfa",
    "erythropoietin": "epoetin alfa", "docosahexanoic acid": "doconexent", "docosahexaenoic acid": "doconexent",
    "cyproterone": "cyproterone acetate", "glucosamine sulfate potassium chloride": "glucosamine",
    "coenzyme q10": "ubidecarenone",
    "methyl ergometrine": "methylergometrine", "methylergonovine": "methylergometrine",
    "fluoromethalone": "fluorometholone", "levocetrizine": "levocetirizine", "isosorbidemononitrate": "isosorbide mononitrate",
    "5-flurouracil": "fluorouracil", "5-fluorouracil": "fluorouracil", "flurouracil": "fluorouracil",
    "biphasic isophane insulin": "insulin human", "natural micronised progesterone": "progesterone",
    "micronised progesterone": "progesterone", "liposomal amphotericin b": "amphotericin b",
    "human chorionic gonadotrophin": "chorionic gonadotropin", "glargine": "insulin glargine",
    "biphasic insulin lispro": "insulin lispro",
}
# Same medicine where neither name is in DrugCentral: map to the name DDInter uses, so interactions attach.
RENAME = {"lactobacillus": "lactobacillus acidophilus", "lactic acid bacillus": "lactobacillus acidophilus"}
# DrugCentral synonyms that would merge different products; these keep their own key.
DENY = {"tinzaparin", "dalteparin sodium", "dalteparin", "sodium ferric gluconate complex", "edetate disodium",
        "ardeparin", "urofollitropin", "norgestrel",
        "edetate calcium disodium"}
QUALIFIER = re.compile(r"\s*\([^)]*\)\s*$")   # DDInter route/form tags: "(ophthalmic)", "(regular)"


def norm(text):
    text = (text or "").replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_salts(name):
    words = name.split()
    while len(words) > 1 and words[-1] in SALT_WORDS:
        words.pop()
    return " ".join(words)


def salt_steps(name):
    """'x hydrochloride monohydrate' -> ['x hydrochloride monohydrate', 'x hydrochloride', 'x']"""
    words, out = name.split(), [name]
    while len(words) > 1 and words[-1] in SALT_WORDS:
        words.pop()
        out.append(" ".join(words))
    return out


def base_name(name):
    """Cleaned name used as the key when a drug is not in DrugCentral."""
    n = QUALIFIER.sub("", norm(name)) or norm(name)
    n = RENAME.get(n, n)
    stripped = strip_salts(n)
    return n if stripped in STEM_JUNK or len(stripped) < 4 or stripped.endswith(" hydrogen") else stripped


class DrugResolver:
    """Maps any drug name (Indian, American, brand-salt form, route-tagged) to a single key."""

    def __init__(self):
        self.dc_name = {}   # DrugCentral id -> official name
        self.lookup = {}    # lower-case name or synonym -> DrugCentral id
        for r in read_tsv(DATA / "drugcentral/drugcentral_structures.tsv"):
            self.dc_name[r["id"]] = r["name"]
            self.lookup[norm(r["name"])] = r["id"]
        synonyms = defaultdict(set)
        for r in read_tsv(DATA / "drugcentral/drugcentral_synonyms.tsv"):
            if r["id"] in self.dc_name:
                synonyms[norm(r["lname"] or r["name"])].add(r["id"])
        for name, ids in synonyms.items():
            if len(ids) == 1 and name not in DENY:         # a synonym shared by two drugs is ambiguous
                self.lookup.setdefault(name, next(iter(ids)))
        # also every name with its salt/ester removed, without overriding an exact name
        for name, dc in list(self.lookup.items()):
            for step in salt_steps(name)[1:]:
                if step not in STEM_JUNK and len(step) >= 4:
                    self.lookup.setdefault(step, dc)
        for alias, target in ALIASES.items():
            if norm(target) in self.lookup:
                self.lookup[alias] = self.lookup[norm(target)]
        self._cache = {}
        self.seen = {}      # raw name -> key, for the merge log

    def dc_id(self, name):
        n = norm(name)
        if n not in self._cache:
            self._cache[n] = None if n in DENY else self._find(n)
        return self._cache[n]

    def _find(self, n):
        forms = [n, QUALIFIER.sub("", n)]
        forms += [p.strip() for f in list(forms) if "/" in f for p in f.split("/")]      # "a/b" = two spellings
        forms += [f.replace("sulph", "sulf") for f in forms] + [f.replace("alpha", "alfa") for f in forms]
        for f in dict.fromkeys(forms):
            if f in DENY:
                return None
            for c in salt_steps(f):
                if c in self.lookup:
                    return self.lookup[c]
        for f in dict.fromkeys(forms):                        # only now drop a leading salt word
            words = f.split()
            if len(words) > 1 and words[0] in LEADING_SALTS:
                for c in salt_steps(" ".join(words[1:])):
                    if c in self.lookup:
                        return self.lookup[c]
        return None

    def key(self, name):
        dc = self.dc_id(name)
        k = f"DC:{dc}" if dc else f"N:{base_name(name)}"
        self.seen[norm(name)] = k
        return k

    def display(self, key, fallback):
        return self.dc_name[key[3:]] if key.startswith("DC:") else base_name(fallback)


def read_tsv(path):
    with open(path, encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f, delimiter="\t")


def write_csv(name, header, rows):
    BUILD.mkdir(exist_ok=True)
    rows = list(rows)
    with open(BUILD / name, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name:<28} {len(rows):>9,} rows")
    return rows


# A ban can be limited to a dosage form or release type ("Aceclofenac (SR) + Paracetamol" bans only the
# sustained-release tablet, not the common one): the brand name must then show it too
BAN_FORMS = {"injection": r"injection|\binj\b", "syrup": r"syrup", "suspension": r"suspension", "drops": r"\bdrops?\b",
             "dispersible": r"dispersible|\bdt\b", "gel": r"\bgel\b", "cream": r"cream", "ointment": r"ointment",
             "lotion": r"lotion", "SR": r"\bsr\b|sustained", "ER": r"\ber\b|extended", "MR": r"\bmr\b|modified",
             "CR": r"\bcr\b|controlled", "XR": r"\bxr\b"}
BAN_STRENGTH = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mcg|gm?|iu|%|units?)\b", re.I)
BAN_WORDS = re.compile(r"\b(?:fixed dose combinations? of|combi ?kit of|kit of|\d+ tablets? of|tablets?|capsules?|"
                       r"syrup|suspension|injection|dispersible|drops|gel|cream|ointment|lotion|per \d+ ?ml|"
                       r"enteric coated|sr|er|mr|cr|xr|ip|bp|usp)\b", re.I)
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def banned_combinations(res, brands, contains):
    """Indian brands whose ingredients are a combination banned by CDSCO (data/cdsco/banned_fdcs.csv).
    Precision first, since a wrong "banned" label is worse than a missed one. A brand matches only when:
    its ingredients are exactly the banned set (brands whose composition the source cut off are never matched,
    their missing ingredient is unknown), its name shows any form or release type the ban names, and, for a
    ban on particular strengths, its name lists those strengths. Rows whose ban is not in force are skipped."""
    known = sorted(res.lookup)

    def ingredient(part):
        name = BAN_WORDS.sub(" ", BAN_STRENGTH.sub(" ", re.sub(r"\([^)]*\)", " ", part)))
        name = re.sub(r"\s+", " ", NUMBER.sub(" ", name)).strip(" .,&-/")
        if not name:
            return None
        if not res.dc_id(name):                                   # typos in the gazette ("Acelofenac")
            close = difflib.get_close_matches(norm(name), known, n=1, cutoff=0.88)
            name = close[0] if close else name
        return res.key(name)

    parts = defaultdict(set)
    for bk, dk, _ in contains:
        if bk.startswith("IN:"):
            parts[bk].add(dk)
    complete = {b[0]: b[1] for b in brands if b[0].startswith("IN:") and not b[11]}
    by_set = defaultdict(list)                                     # ingredient set -> complete Indian brands
    for bk, keys in parts.items():
        if bk in complete:
            by_set[frozenset(keys)].append(bk)

    bans, banned = [], set()
    with open(DATA / "cdsco/banned_fdcs.csv", encoding="utf-8", newline="") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            if r["status"] != "banned":
                continue
            text = r["combination"]
            listed = [k for k in (ingredient(p) for p in re.split(r"\s*\+\s*", text)) if k]
            keys = frozenset(listed)
            # two listed salts of one drug ("metoprolol succinate + metoprolol tartrate") would merge into one
            # ingredient and wrongly match the ordinary product: skip such a ban
            if len(keys) < 2 or len(keys) < len(listed) or not by_set.get(keys):
                continue
            forms = [f for f, pat in BAN_FORMS.items() if re.search(pat, text, re.I)]
            doses = {float(n) for m in BAN_STRENGTH.finditer(text) for n in NUMBER.findall(m.group(0))}
            key = f"BAN:{i}"
            hits = []
            for bk in by_set[keys]:
                name = complete[bk]
                if forms and not any(re.search(BAN_FORMS[f], name, re.I) for f in forms):
                    continue
                if doses:                                           # strength-specific ban
                    own = [float(n) for n in NUMBER.findall(" ".join(m.group(0) for m in BAN_STRENGTH.finditer(name)))]
                    if len(own) != len(keys) or not set(own) <= doses:
                        continue
                hits.append(bk)
            if hits:
                bans.append((key, text, r["notification"], r["date"], r["list"], "/".join(forms)))
                banned.update((bk, key) for bk in hits)
    return bans, sorted(banned)


# Label sections searched for sentences about other medicines, most important first
EVIDENCE_SECTIONS = ["boxed_warning", "contraindications", "drug_interactions", "warnings_and_cautions", "warnings"]
LABEL_SENTENCE = re.compile(r"(?<=[.;])\s+(?=[A-Z(])")
LABEL_REFS = re.compile(r"\[\s*see [^\]]*\]|\(\s*\d+(?:\.\d+)*\s*(?:,\s*\d+(?:\.\d+)*\s*)*\)", re.I)
LABEL_CLASSES = [(re.compile(rf"\b(?:{pattern})\b", re.I), classes) for pattern, classes in LABEL_CLASS_TERMS.items()]
# single words that are also drug synonyms but in a label usually mean something else
NOT_A_MENTION = {"iron", "water", "oxygen", "gold", "lead", "sodium", "potassium", "calcium", "magnesium", "zinc",
                 "glucose", "dextrose", "protein", "vitamin", "salt", "sugar", "caffeine"}
MENTIONS_PER_PAIR = 3


def label_sentences(text):
    for sentence in LABEL_SENTENCE.split(re.sub(r"\s+", " ", LABEL_REFS.sub("", text))):
        sentence = sentence.strip()
        letters = [c for c in sentence if c.isalpha()]
        # boxed warnings list their headings in capitals without full stops ("USE WITH BENZODIAZEPINES OR ..."),
        # which would run together into a garbled quote
        if 30 <= len(sentence) <= 600 and sum(c.isupper() for c in letters) <= 0.4 * len(letters):
            yield sentence


def label_evidence(res, graph_drugs):
    """openFDA labels (data/fda/labels.jsonl.gz): one Label per drug, and the sentences in drug A's label that
    name drug B ((A)-[:LABEL_MENTIONS]->(B)) or a group B belongs to (kept on the label, matched at query time)."""
    labels, mentions = [], {}
    with gzip.open(DATA / "fda/labels.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            lab = json.loads(line)
            a, sections = lab["drug"], lab["sections"]
            if a not in graph_drugs:
                continue
            class_notes = []
            for section in EVIDENCE_SECTIONS:
                for sentence in label_sentences(sections.get(section, "")):
                    words = re.findall(r"[a-z0-9][a-z0-9'\-]*", sentence.lower())
                    named = set()
                    for n in (1, 2, 3, 4):
                        for i in range(len(words) - n + 1):
                            gram = " ".join(words[i:i + n])
                            if n == 1 and (len(gram) < 5 or gram in NOT_A_MENTION):
                                continue
                            if gram in res.lookup and f"DC:{res.lookup[gram]}" in graph_drugs:
                                named.add(f"DC:{res.lookup[gram]}")
                    for b in named - {a}:
                        found = mentions.setdefault((a, b), [])
                        if len(found) < MENTIONS_PER_PAIR and sentence not in found:
                            found.append(sentence)
                    classes = sorted({c for pattern, cs in LABEL_CLASSES if pattern.search(sentence) for c in cs})
                    if classes and len(class_notes) < 60:
                        class_notes.append({"classes": classes, "text": sentence})
            labels.append((a, lab["set_id"], lab["effective"], lab["substance"], lab["brand"],
                           *(sections.get(k, "") for k in ("boxed_warning", "indications_and_usage", "contraindications",
                                                           "information_for_patients", "geriatric_use", "pregnancy")),
                           json.dumps(class_notes, ensure_ascii=False)))
    return labels, [(a, b, json.dumps(t, ensure_ascii=False)) for (a, b), t in sorted(mentions.items())]


def beers_guidelines(res, drug_names, drug_classes):
    """AGS Beers Criteria 2023 (data/beers/beers_2023.csv, see src/make_beers.py): one Guideline per criterion and
    (Drug)-[:FLAGGED_BY {side}]->(Guideline) for every drug it names or whose class it names. A name that matches
    no drug stops the build, so a typo cannot silently drop a warning."""
    by_name = {n.lower(): k for k, n in drug_names.items()}
    with open(DATA / "beers/beers_2023.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    table7 = next(r["drugs"] for r in rows if r["id"] == "T7")
    split = lambda text: [x.strip() for x in text.replace("@table7", table7).split(";") if x.strip()]

    def keys(names, atc, exclude):
        out = set()
        for name in split(names):
            k = by_name.get(name.lower()) or (f"DC:{res.dc_id(name)}" if res.dc_id(name) else None)
            if k not in drug_names:
                raise SystemExit(f"Beers criteria: no drug named {name!r} (fix src/make_beers.py)")
            out.add(k)
        prefixes = split(atc)
        out |= {k for k in drug_names if any(c.startswith(p) for c in drug_classes.get(k, ()) for p in prefixes)}
        return out - keys(exclude, "", "") if exclude else out

    guidelines, flagged = [], []
    for r in rows:
        if r["kind"] == "list":
            continue
        guidelines.append((f"BEERS:{r['id']}", r["table"], r["page"], r["kind"], r["title"], r["advice"], r["reason"],
                           r["evidence"], r["points"], r["condition"], r["min_count"]))
        flagged += [(k, f"BEERS:{r['id']}", "a") for k in sorted(keys(r["drugs"], r["atc"], r["exclude"]))]
        if r["kind"] == "pair":
            flagged += [(k, f"BEERS:{r['id']}", "b") for k in sorted(keys(r["drugs_b"], r["atc_b"], r["exclude"]))]
    return guidelines, flagged


def main():
    print("Building clean CSVs in build/ ...")
    res = DrugResolver()
    drug_names = {}                      # key -> display name
    sources = defaultdict(set)           # key -> which datasets mention the drug

    def drug(name, source):
        k = res.key(name)
        drug_names.setdefault(k, res.display(k, name))
        sources[k].add(source)
        return k

    # --- DDInter: drug-drug interactions (14 files overlap, keep the most severe level per pair)
    pairs = {}
    for f in sorted(glob.glob(str(DATA / "ddinter/*.csv"))):
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                a, b = drug(r["Drug_A"], "ddinter"), drug(r["Drug_B"], "ddinter")
                if a == b:
                    continue
                pair = tuple(sorted((a, b)))
                if SEVERITY_RANK[r["Level"]] >= SEVERITY_RANK.get(pairs.get(pair), -1):
                    pairs[pair] = r["Level"]
    write_csv("interactions.csv", ["a", "b", "severity"], ((a, b, s) for (a, b), s in pairs.items()))

    # --- FDA table: which drugs block, speed up, or depend on which liver enzyme
    columns = {
        "CYP Strg INH": ("INHIBITS", "strong"), "CYP Mod INH": ("INHIBITS", "moderate"),
        "CYP WK INH": ("INHIBITS", "weak"), "CYP Strg IND": ("INDUCES", "strong"),
        "CYP Mod IND": ("INDUCES", "moderate"), "CYP WK IND": ("INDUCES", "weak"),
        "CYP SENS SUB": ("METABOLISED_BY", "sensitive"), "CYP Mod SENS SUB": ("METABOLISED_BY", "moderate"),
    }
    enzyme_code = re.compile(r"(1A2|2B6|2C8|2C19|2C9|2D6|3A)")
    effects = set()
    for r in read_tsv(DATA / "fda/fda_cyp_transporter_interacting_drugs.tsv"):
        name = re.sub(r"[\d,\s]+$", "", r["Drug or Other Substance"]).strip()   # drop footnote numbers
        if " and " in name or not (res.dc_id(name) or res.key(name) in sources):
            continue   # combination products and non-medicines (grapefruit, tobacco) are skipped
        k = drug(name, "fda")
        for col, (rel, strength) in columns.items():
            for code in set(enzyme_code.findall(r[col])):
                effects.add((k, "CYP" + code, rel, strength))
    write_csv("enzyme_effects.csv", ["drug", "enzyme", "rel", "strength"], sorted(effects))
    write_csv("enzymes.csv", ["name"], ([e] for e in sorted({e[1] for e in effects})))

    # --- DrugCentral: conditions a drug treats or must not be used in, and drug classes (ATC)
    conditions, treats, contra = {}, set(), set()
    for r in read_tsv(DATA / "drugcentral/drugcentral_omop_relationship.tsv"):
        if r["relationship_name"] not in ("indication", "contraindication") or r["struct_id"] not in res.dc_name:
            continue
        ck = f"C:{r['umls_cui']}" if r["umls_cui"] else f"C:OMOP{r['concept_id']}"
        conditions.setdefault(ck, (r["concept_name"], r["umls_cui"]))
        k = f"DC:{r['struct_id']}"
        drug_names.setdefault(k, res.dc_name[r["struct_id"]])
        sources[k].add("drugcentral")
        (treats if r["relationship_name"] == "indication" else contra).add((k, ck))
    write_csv("conditions.csv", ["key", "name", "cui"], ((k, n, c) for k, (n, c) in conditions.items()))
    write_csv("treats.csv", ["drug", "condition"], sorted(treats))
    write_csv("contraindicated_in.csv", ["drug", "condition"], sorted(contra))

    atc_names = {r["l4_code"]: r["l4_name"] for r in read_tsv(DATA / "drugcentral/drugcentral_atc.tsv")}
    # "Combination" classes group unrelated drugs sold together (e.g. opioid + paracetamol), so they
    # must not count as two drugs being the same type of medicine.
    atc_rows = list(read_tsv(DATA / "drugcentral/drugcentral_atc.tsv"))
    combination = {r["l4_code"] for r in atc_rows
                   if "combination" in (r["l4_name"] + " " + r["l3_name"]).lower() or r["l3_code"] == "C08G"}
    drug_classes = defaultdict(set)
    for r in read_tsv(DATA / "drugcentral/drugcentral_struct2atc.tsv"):
        # a level-5 code ending in 50-59 or 70-79 is a combination product entry, not the drug's own class
        if r["atc_code"][:5] not in combination and r["atc_code"][5:6] not in ("5", "7"):
            drug_classes[f"DC:{r['struct_id']}"].add(r["atc_code"][:5])

    # --- Hetionet: side effects each drug causes (side effects carry the same UMLS codes as conditions)
    compound, side_effects = {}, {}
    for line in open(DATA / "hetionet/hetionet-v1.0-nodes.tsv", encoding="utf-8"):
        node_id, name, kind = line.rstrip("\r\n").split("\t")
        if kind == "Compound":
            compound[node_id] = name
        elif kind == "Side Effect":
            side_effects[node_id] = name
    causes = set()
    with gzip.open(DATA / "hetionet/hetionet-v1.0-edges.sif.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            src, metaedge, dst = line.rstrip("\r\n").split("\t")
            if metaedge == "CcSE" and src in compound:
                causes.add((drug(compound[src], "hetionet"), "SE:" + dst.split("::")[1]))
    write_csv("side_effects.csv", ["key", "name", "cui"],
              ((f"SE:{i.split('::')[1]}", n, i.split("::")[1]) for i, n in side_effects.items()))
    write_csv("causes.csv", ["drug", "side_effect"], sorted(causes))

    # --- Indian brands: brand -> active ingredients
    # The source has only two composition columns, so a 3-ingredient product keeps two. We flag products whose
    # own name lists 3+ strengths ("10mg/325mg/37.5mg") but have 2 ingredients; we do NOT guess the missing one.
    strengths = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mcg|gm?|iu|%|ml|kg)?(?:\s*/\s*\d+(?:\.\d+)?\s*(?:mg|mcg|gm?|iu|%|ml|kg)?)+", re.I)

    def strength_count(name):
        runs = [[p for p in m.group(0).split("/") if not re.search(r"ml\s*$", p.strip(), re.I)]
                for m in strengths.finditer(name)]       # "/5ml" is a concentration, not an ingredient
        return max((len(r) for r in runs), default=0)

    brands, contains, incomplete = [], [], 0
    with open(DATA / "india/A_Z_medicines_dataset_of_India.csv", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            bk = f"IN:{r['id']}"
            parts = [p for p in (r["short_composition1"], r["short_composition2"]) if p.strip()]
            if not parts:
                continue
            name = re.sub(r"\s+", " ", r["name"]).strip()
            partial = len(parts) == 2 and strength_count(name) >= 3
            incomplete += partial
            brands.append((bk, name, r["manufacturer_name"], r["price(₹)"], r["Is_discontinued"] == "TRUE",
                           r["type"], r["pack_size_label"], "india_az", False, "", "", partial))
            for p in parts:
                m = re.match(r"\s*(.*?)\s*(?:\((.*?)\))?\s*$", p)
                contains.append((bk, drug(m.group(1), "india_az"), (m.group(2) or "").strip()))
    print(f"  (Indian brands flagged 'composition may be incomplete': {incomplete:,})")
    # --- 250k Indian medicines: plain-language uses and side effects, habit forming, same-composition substitutes.
    # Matched to the A-Z brands by name (same publisher); a substitute becomes (Brand)-[:SUBSTITUTE]->(Brand), but
    # only when the two really have the same ingredients (0.3% of the source's substitutes do not, e.g.
    # levofloxacin listed for ciprofloxacin) and no known strength differs (Cefritz S 1000 mg/500 mg is not
    # Monotax SB 250 mg/125 mg). The link records whether every strength was known on both sides.
    ingredients, strength = defaultdict(set), defaultdict(dict)
    for bk, dk, st in contains:
        ingredients[bk].add(dk)
        amount = re.match(r"[\d.]+\s*(?:mg|mcg|gm?|iu|%|ml)?", re.sub(r"\s+", "", st.lower()))
        strength[bk][dk] = amount.group(0) if amount and amount.group(0) else None
    by_name = defaultdict(list)
    for b in brands:
        by_name[norm(b[1])].append(b[0])
    details, substitutes = [], set()
    with gzip.open(DATA / "india/medicine_details.csv.gz", "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            for bk in by_name.get(norm(r["name"]), []):
                details.append((bk, r["uses"], r["side_effects"], r["habit_forming"], r["therapeutic_class"],
                                r["action_class"], r["chemical_class"]))
                for sub in r["substitutes"].split("|"):
                    for sk in by_name.get(norm(sub), []):
                        if sk == bk or ingredients[sk] != ingredients[bk]:
                            continue
                        pairs = [(strength[bk].get(d), strength[sk].get(d)) for d in ingredients[bk]]
                        if any(a and b and a != b for a, b in pairs):
                            continue
                        substitutes.add((bk, sk, all(a and b for a, b in pairs)))
    write_csv("brand_details.csv", ["brand", "uses", "side_effects", "habit_forming", "therapeutic_class",
                                    "action_class", "chemical_class"], details)
    write_csv("substitutes.csv", ["brand", "substitute", "same_strength"], sorted(substitutes))

    bans, banned = banned_combinations(res, brands, contains)
    write_csv("bans.csv", ["key", "combination", "notification", "date", "list", "form"], bans)
    write_csv("banned_under.csv", ["brand", "ban"], banned)
    print(f"  (Indian brands matching a combination banned in India: {len({b for b, _ in banned}):,})")

    # --- Jan Aushadhi generics: parse ingredient names out of the product description.
    # Stop words must match whole words ("g" must not cut "glimepiride"); numbers and % stop anywhere.
    stop = re.compile(r"^(\d(?!-[a-z])|%|(?:tablets?|capsules?|injection|syrup|suspension|gel|cream|ointment|drops?|solution|"
                      r"ip|bp|usp|sr|er|xr|cr|mr|dt|oral|eye|ear|nasal|spray|lotion|powder|sachets?|inhaler|inhalation|"
                      r"respules|rotacaps|kit|film|coated|extended|release|prolonged|dispersible|chewable|sustained|"
                      r"modified|delayed|gastro|resistant|enteric|soft|gelatin|hard|for|infusion|vial|mg|mcg|gm?|ml|iu|"
                      r"ophthalmic|transdermal|patch|suppositor(?:y|ies)|pessar(?:y|ies)|per|w/w|w/v|emulsion|elixir|"
                      r"prolonged-release|sustained-release|extended-release|gastro-resistant|dry|mouth|wash|paste|"
                      r"topical|vaginal|rectal|each|containing|contains|oil|jelly|granules|lozenges?|shampoo|soap)(?![a-z]))",
                      re.I)
    alcohol = res.key("ethanol")
    ja_skipped = []
    with open(DATA / "jan_aushadhi/jan_aushadhi_product_mrp_list_2026-09-30.csv", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if "Surgical" in r["Group Name"]:
                continue
            text = re.sub(r"^\s*(combi\s*-?\s*pack|combi\s*kit|kit)\s+of\s+", "", r["Generic Name"], flags=re.I)
            pieces = [re.sub(r"\([^)]*\)", " ", text)] + re.findall(r"\(([^)]*)\)", text)   # bracket contents too
            found, tried = {}, []
            for piece in pieces:
                for part in re.split(r"\s+and\s+|\s*&\s*|\s*\+\s*|,\s*|\s+with\s+|\s*/\s*", piece, flags=re.I):
                    words = []
                    for w in part.split():
                        if stop.match(w):
                            break
                        words.append(w)
                    if not words:
                        continue
                    # unknown form words may follow the name ("Orally Disintegrating Strips"): use the longest
                    # leading part that is a known drug; a single word only if it is a real drug name
                    for n in range(len(words), 0, -1):
                        name = " ".join(words[:n])
                        if n == 1 and (len(name) < 6 or name.lower() in LEADING_SALTS | STEM_JUNK):
                            break
                        if res.dc_id(name):
                            break
                    else:
                        name = None
                    tried.append(" ".join(words))
                    if name and res.dc_id(name) and res.key(name) != alcohol:   # sanitisers are not medicines here
                        found.setdefault(res.key(name), name)
            if not found:
                reason = ("nutraceutical / supplement" if re.search(r"nutra|supplement|food", r["Group Name"], re.I)
                          else "no medicine name recognised")
                ja_skipped.append((r["Drug Code"], r["Generic Name"], r["Group Name"], reason, "; ".join(tried)))
                continue
            bk = f"JA:{r['Drug Code']}"
            brands.append((bk, r["Generic Name"], "Jan Aushadhi (PMBI)", r["MRP"], False, "generic", r["Unit Size"],
                           "jan_aushadhi", True, r["Group Name"], r["Unit Size"], False))
            contains.extend((bk, drug(i, "jan_aushadhi"), "") for i in found.values())
    write_csv("brands.csv", ["key", "name", "manufacturer", "price", "discontinued", "type", "pack",
                             "source", "jan_aushadhi", "group", "unit_size", "composition_incomplete"], brands)
    write_csv("contains.csv", ["brand", "drug", "strength"], contains)
    write_csv("ja_skipped.csv", ["drug_code", "generic_name", "group", "reason", "names_tried"], ja_skipped)

    # --- Merge log: keys that several different raw names were mapped to (checked by hand after each change)
    raw_names = defaultdict(set)
    for raw, k in res.seen.items():
        raw_names[k].add(raw)
    write_csv("merge_log.csv", ["key", "name", "raw_names"],
              ((k, drug_names.get(k, ""), " | ".join(sorted(v))) for k, v in sorted(raw_names.items()) if len(v) > 1))

    # --- openFDA labels: what the official label says, and which other medicines each label warns about
    labels, mentions = label_evidence(res, drug_names)
    write_csv("labels.csv", ["drug", "set_id", "effective", "substance", "brand", "boxed_warning", "indications",
                             "contraindications", "patient_info", "geriatric_use", "pregnancy", "class_notes"], labels)
    write_csv("label_mentions.csv", ["a", "b", "sentences"], mentions)

    # --- TWOSIDES: side effects reported far more often when two drugs are taken together (FDA reports)
    together = defaultdict(list)
    with gzip.open(DATA / "twosides/twosides_pairs.csv.gz", "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["a"] in drug_names and r["b"] in drug_names:
                together[(r["a"], r["b"])].append((r["effect"], int(r["reports"]), float(r["prr"])))
    write_csv("reported_together.csv", ["a", "b", "effects", "reports", "prr"],
              ((a, b, json.dumps([e for e, _, _ in v]), json.dumps([n for _, n, _ in v]), json.dumps([p for _, _, p in v]))
               for (a, b), v in sorted(together.items())))

    # --- AGS Beers Criteria 2023: medicines to avoid or use with care in adults aged 65 and over
    guidelines, flagged = beers_guidelines(res, drug_names, drug_classes)
    write_csv("guidelines.csv", ["key", "table", "page", "kind", "title", "advice", "reason", "evidence", "points",
                                 "condition", "min_count"], guidelines)
    write_csv("flagged_by.csv", ["drug", "guideline", "side"], flagged)

    # --- Drugs: one row per key, with drug class codes for duplicate detection
    write_csv("drugs.csv", ["key", "name", "dc_id", "atc_classes", "atc_class_names", "sources", "is_alcohol"],
              ((k, n, k[3:] if k.startswith("DC:") else "", ";".join(sorted(drug_classes.get(k, ()))),
                ";".join(sorted({atc_names.get(c, "") for c in drug_classes.get(k, ())} - {""})),
                ";".join(sorted(sources[k])), n.lower() in ("ethanol", "alcohol"))
               for k, n in sorted(drug_names.items())))

    # --- Drug classes: the top two ATC levels as nodes, (Drug)-[:BELONGS_TO]->(level 2)-[:PART_OF]->(level 1).
    # Built from the same classes as above (combination classes left out), so the class map counts real drug types.
    names = {}
    for r in atc_rows:
        names[r["l1_code"]] = r["l1_name"]
        names[r["l2_code"]] = r["l2_name"]
    belongs = sorted({(k, c[:3]) for k, classes in drug_classes.items() if k in drug_names for c in classes})
    used = sorted({c for _, c in belongs})
    groups = sorted({c[0] for c in used})
    write_csv("drug_classes.csv", ["code", "level", "name", "atc_name", "parent"],
              [(g, 1, ATC_GROUPS[g], names[g].capitalize(), "") for g in groups] +
              [(c, 2, names[c].capitalize(), names[c].capitalize(), c[0]) for c in used])
    write_csv("belongs_to.csv", ["drug", "class"], belongs)
    print("Done.")


if __name__ == "__main__":
    main()
