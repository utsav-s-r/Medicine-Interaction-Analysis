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
    print("--- combinations banned in India (CDSCO)")
    r = analyse(["Scofix AZ 200mg/250mg Tablet"])
    check("azithromycin + cefixime brand flagged as banned in India (2018)",
          kinds(r, "Banned in India") and r["level"]["level"] == "Critical", (kinds(r, "Banned in India"), r["level"]))
    check("ordinary aceclofenac + paracetamol (Zerodol-P) is not banned", not kinds(analyse(["Zerodol-P"]), "Banned in India"))
    check("sustained-release aceclofenac + paracetamol (Gloar-SR) is banned",
          bool(kinds(analyse(["Gloar-SR Tablet"]), "Banned in India")))
    check("a ban quashed in court is not applied (pioglitazone + metformin)",
          not kinds(analyse(["Asoformin P 15mg/500mg Tablet"]), "Banned in India"))
    bans = read("bans.csv")
    check("bans listing two salts of one drug are skipped (cilnidipine + metoprolol)",
          not any("metoprolol" in b["combination"].lower() for b in bans), [b["combination"] for b in bans if "etoprolol" in b["combination"]])
    print("--- official labels (openFDA) and reports (TWOSIDES)")
    labels = d.execute_query("MATCH (:Drug)-[:HAS_LABEL]->(l:Label) RETURN count(l) AS n").records[0]["n"]
    check("most drugs have an official label", labels > 1200, labels)
    r = analyse(["warfarin", "Brufen 400"])
    quotes = [q["text"] for f in r["findings"] for q in f["label"]]
    check("warfarin + ibuprofen quotes a label sentence about bleeding", any("bleed" in q.lower() for q in quotes), quotes)
    about = {a["drug"]: a for a in r["about"]}
    check("warfarin's boxed warning is 'Bleeding risk'", about.get("warfarin", {}).get("boxed_title") == "Bleeding risk",
          about.get("warfarin"))
    check("paracetamol finds its US label under the name acetaminophen", bool(analyse(["Dolo 650"])["about"][0]["url"]))
    serious = re.compile(queries.REPORTED_EFFECTS)
    shown = [e["effect"] for m in (["warfarin", "Brufen 400"], ["clozapine", "citalopram"], ["metformin", "glimepiride"])
             for f in analyse(m)["findings"] for e in f["reported"]]
    check("only serious, recognisable reported side effects are shown", shown and all(serious.search(e) for e in shown),
          [e for e in shown if not serious.search(e)])
    print("--- Indian brand details (250k Indian medicines)")
    brands = {b["name"]: b for b in analyse(["Alprax 0.25 Tablet", "Dolo 650", "Augmentin 625 Duo"])["brands"]}
    check("alprazolam brand flagged habit forming", brands.get("Alprax 0.25 Tablet", {}).get("habit_forming") is True)
    check("paracetamol brand says what it is used for", "Treatment of Fever" in brands.get("Dolo 650 Tablet", {}).get("uses", []),
          brands.get("Dolo 650 Tablet", {}).get("uses"))
    subs = brands.get("Augmentin 625 Duo Tablet", {}).get("subs", [])
    check("substitutes are offered, cheapest per tablet first",
          len(subs) == 3 and [s["unit"][0] for s in subs] == sorted(s["unit"][0] for s in subs), subs)
    wrong = d.execute_query("""
        MATCH (b:Brand)-[:SUBSTITUTE]->(s:Brand)
        WHERE COUNT { (b)-[:CONTAINS]->() } <> COUNT { (s)-[:CONTAINS]->() }
           OR EXISTS { (b)-[:CONTAINS]->(x) WHERE NOT (s)-[:CONTAINS]->(x) }
        RETURN count(*) AS n""").records[0]["n"]
    check("every substitute has exactly the same ingredients (levofloxacin is not ciprofloxacin)", wrong == 0, wrong)
    amount = lambda st: (re.match(r"[\d.]+(?:mg|mcg|gm?|iu|%|ml)?", re.sub(r"\s+", "", st.lower())) or [None])[0]
    strengths = defaultdict(dict)
    for c in read("contains.csv"):
        strengths[c["brand"]][c["drug"]] = amount(c["strength"])
    clash = [s for s in read("substitutes.csv")
             if any(a and b and a != b for a, b in ((v, strengths[s["substitute"]].get(k))
                                                    for k, v in strengths[s["brand"]].items()))]
    check("no substitute differs in a known strength (1000/500 mg is not 250/125 mg)", not clash, clash[:3])
    print("--- older adults (AGS Beers Criteria 2023)")
    beers = lambda r: [f["title"] for f in r["findings"] if "over 65" in f["kind"]]
    at = lambda age, meds, conds=(): queries.analyse(d, list(meds), list(conds), None, age)
    check("diazepam flagged at 72 but not at 40 or without an age",
          any("diazepam is not advised" in t for t in beers(at(72, ["diazepam"])))
          and not beers(at(40, ["diazepam"])) and not beers(at(None, ["diazepam"])))
    r = at(72, ["diazepam", "tramadol", "pregabalin"])
    check("three brain-acting medicines flagged, counting pregabalin (ATC moved it to N02BF)",
          any(t.startswith("Three or more medicines acting on the brain") for t in beers(r)), beers(r))
    opioid_benzo = next((f for f in r["findings"] if "tramadol" in f["title"] and "diazepam" in f["title"]), None)
    check("an opioid + benzodiazepine already in DDInter gets the Beers advice, not double points",
          opioid_benzo and opioid_benzo["kind"] == "Drug interaction" and opioid_benzo["beers"], opioid_benzo)
    r = at(70, ["amitriptyline", "promethazine"])
    check("two anticholinergic medicines flagged", any(t.startswith("Two or more anticholinergic") for t in beers(r)),
          beers(r))
    check("a Beers condition warning needs the condition (sertraline + falls)",
          any("fall" in t for t in beers(at(78, ["sertraline"], ["falls"]))) and not beers(at(78, ["sertraline"])))
    check("situation-dependent criteria score 1, not 2 (pantoprazole)",
          [f["weight"] for f in at(78, ["pantoprazole"])["findings"] if "over 65" in f["kind"]] == [1])
    check("use-with-care criteria are notes, not scored (tramadol lowers sodium)",
          any("sodium" in c["title"] for c in at(78, ["tramadol"])["cautions"]) and at(78, ["tramadol"])["score"] == 0)
    check("lithium is not counted as an antipsychotic",
          not any("lithium" in t for t in beers(at(78, ["lithium carbonate"]))))
    print("--- risk graph (/graph Risks view)")
    import explore  # noqa: E402
    r = queries.analyse(d, ["warfarin", "Brufen 400", "clarithromycin", "simvastatin", "Scofix AZ 200mg/250mg Tablet",
                            "Dolo 650", "Calpol 500", "amlodipine", "furosemide"], ["kidney"], None, 78)
    g = explore.risk_graph(d, r)
    noise = {"LABEL_MENTIONS", "REPORTED_TOGETHER", "SUBSTITUTE", "HAS_LABEL"} & {e["type"] for e in g["edges"]}
    check("the risk graph holds no noise relationships (reports, label mentions, substitutes)", not noise, noise)
    check("every warning has a path in the graph", all(f["edges"] for f in g["findings"]),
          [f["title"] for f in g["findings"] if not f["edges"]])
    check("every edge and every non-medicine node belongs to a warning",
          all(e["findings"] or e["type"] == "CONTAINS" for e in g["edges"])
          and all(n["findings"] or n["label"] in ("Drug", "Brand") for n in g["nodes"]))
    by_title = {f["title"]: f for f in g["findings"]}
    ban = next(f for f in g["findings"] if f["kind"] == "Banned in India")
    check("the banned brand's path reaches its CDSCO notification",
          any(n.startswith("Ban:") for n in ban["nodes"]), ban["nodes"])
    beers = [f for f in g["findings"] if "over 65" in f["kind"]]
    check("Beers warnings reach their guideline", beers and all(any(n.startswith("Guideline:") for n in f["nodes"])
                                                               for f in beers))
    pair = by_title.get("ibuprofen + warfarin: Major interaction")
    edge = next((e for e in g["edges"] if pair and e["id"] in pair["edges"]), None)
    check("an interaction is one INTERACTS_WITH edge carrying its severity",
          edge and edge["type"] == "INTERACTS_WITH" and edge["props"].get("severity") == "Major", edge)
    check("real nodes carry their database id so their details can load",
          all(n.get("eid") for n in g["nodes"] if not n["derived"]))
    # ticlopidine blocks CYP2C19, which clears voriconazole, and DDInter has no direct record of the pair
    enzyme = [f for f in explore.risk_graph(d, queries.analyse(d, ["ticlopidine", "voriconazole"], []))["findings"]
              if f["kind"] == "Hidden enzyme interaction"]
    check("a hidden enzyme warning's path goes through the enzyme (ticlopidine -> CYP2C19 <- voriconazole)",
          enzyme and "Enzyme:CYP2C19" in enzyme[0]["nodes"] and len(enzyme[0]["edges"]) == 2, enzyme)
    top = queries.top_risk_medicines(d, 5)
    check("GDS risk ranking is populated", len(top) == 5 and top[0]["risk_score"] > 1000, top[:2])
    d.close()

    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
