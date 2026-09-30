"""Turn the raw downloads in data/ into clean node and relationship CSVs in build/.

Every medicine is identified by one key, so the same drug coming from different
sources ends up as a single Drug node:
  DC:<id>   the drug was found in DrugCentral (by its name or one of its synonyms)
  N:<name>  the drug is not in DrugCentral, so it is keyed by its cleaned name

Run:  python src/prepare_data.py
"""
import csv
import glob
import gzip
import re
import sys
from collections import defaultdict
from pathlib import Path

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

    # --- Drugs: one row per key, with drug class codes for duplicate detection
    write_csv("drugs.csv", ["key", "name", "dc_id", "atc_classes", "atc_class_names", "sources", "is_alcohol"],
              ((k, n, k[3:] if k.startswith("DC:") else "", ";".join(sorted(drug_classes.get(k, ()))),
                ";".join(sorted({atc_names.get(c, "") for c in drug_classes.get(k, ())} - {""})),
                ";".join(sorted(sources[k])), n.lower() in ("ethanol", "alcohol"))
               for k, n in sorted(drug_names.items())))
    print("Done.")


if __name__ == "__main__":
    main()
