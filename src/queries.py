"""The six features of the synopsis, as Cypher queries over the medication graph.

analyse() takes what a patient is taking (brand or ingredient names) and their existing
conditions, and returns one ranked assessment. Every finding carries the graph path that
produced it, so each warning can be explained.
"""
import csv
import difflib
import json
import re
from collections import defaultdict
from functools import lru_cache

from config import ROOT
from curated import (ALCOHOL_MEANING, CASCADE_RULES, CONDITION_GROUPS, EFFECT_GROUPS, REPORTED_EFFECTS,
                     SEVERITY_MEANING)
from prepare_data import DrugResolver

SEVERITY_WEIGHT = {"Major": 3, "Moderate": 2, "Minor": 1, "Unknown": 1}
ENZYME_WEIGHT = {"strong": 3, "moderate": 2, "weak": 1}
MAX_CASCADES = 10
LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/&|])')


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip().lower()


def _fulltext(text):
    """'Zerodol-P' -> '(zerodol AND p)^4 OR (zerodol* AND p*)': whole words score above prefixes."""
    terms = [LUCENE_SPECIAL.sub(r"\\\1", t.strip(".")) for t in re.split(r"[^\w.]+", _norm(text)) if t.strip(".")]
    if not terms:
        return None
    return f"({' AND '.join(terms)})^4 OR ({' AND '.join(t + '*' for t in terms)})"


# ---------------------------------------------------------------- resolving what the user typed

def _search(driver, text, index, limit, include_discontinued=False):
    """Ranked full-text hits, one row per distinct name."""
    q = _fulltext(text)
    if not q:
        return []
    return [r.data() for r in driver.execute_query(f"""
        CALL db.index.fulltext.queryNodes('{index}', $q, {{limit: 300}}) YIELD node, score
        WHERE $all OR NOT coalesce(node.discontinued, false)
        WITH node.name AS name, max(score) AS score, max(CASE WHEN node:Drug THEN 1 ELSE 0 END) AS is_drug,
             min(CASE WHEN node:Brand THEN COUNT {{ (node)-[:CONTAINS]->() }} END) AS parts
        RETURN name, score, is_drug = 1 AS is_drug, parts
        ORDER BY score DESC, size(name) LIMIT $limit""", q=q, limit=limit, all=include_discontinued).records]


def suggest(driver, text, kind="medicine", limit=10):
    """Autocomplete for the web page."""
    if kind == "condition":
        groups = [label for label, (said, _) in CONDITION_GROUPS.items()
                  if any(s.startswith(_norm(text)) for s in said + [label.lower()])] if _norm(text) else []
        return (groups + [h["name"] for h in _search(driver, text, "condition_search", limit)])[:limit]
    return [h["name"] for h in _search(driver, text, "medicine_search", limit)]


@lru_cache(maxsize=1)
def _resolver():
    """The same name matching the data build uses (synonyms, salts, esters, Indian spellings)."""
    return DrugResolver()


def _alias_key(text):
    return _resolver().key(text)


def _exact_medicine(driver, name):
    records, _, _ = driver.execute_query("""
        CALL () {
          MATCH (b:Brand {name_lower: toLower($t)})-[:CONTAINS]->(d:Drug)
          WITH b, collect(d {.key, .name}) AS drugs ORDER BY b.discontinued, b.source, b.key
          RETURN 'brand' AS kind, b.name AS label, b.key AS brand_key, drugs,
                 coalesce(b.composition_incomplete, false) AS incomplete,
                 [(b)-[:BANNED_UNDER]->(x:Ban) | x {.combination, .notification, date: toString(x.date), .list}] AS bans
          LIMIT 1
          UNION
          MATCH (d:Drug {name_lower: toLower($t)})
          RETURN 'ingredient' AS kind, d.name AS label, null AS brand_key, [d {.key, .name}] AS drugs,
                 false AS incomplete, [] AS bans LIMIT 1
        }
        RETURN kind, label, brand_key, drugs, incomplete, bans ORDER BY kind DESC LIMIT 1""", t=name.strip())
    return records[0].data() if records else None


def resolve_medicine(driver, text):
    """A typed medicine becomes one or more Drug keys: a brand gives its ingredients."""
    if not text.strip():
        return None
    hit = _exact_medicine(driver, text)
    if not hit and (key := _alias_key(text)):                 # other names for the same drug
        records, _, _ = driver.execute_query("MATCH (d:Drug {key: $k}) RETURN d.name AS name", k=key)
        if records:
            hit = _exact_medicine(driver, records[0]["name"])
    if not hit:                                                # closest search result
        # active products first; a discontinued one only if nothing else matches (old stock at home)
        hits = _search(driver, text, "medicine_search", 10) or _search(driver, text, "medicine_search", 10, True)
        if not hits:
            return None
        best = hits[0]
        if not best["is_drug"] and (best["parts"] or 0) > 1:  # prefer a plain drug over a combination brand
            best = next((h for h in hits if h["is_drug"] and h["score"] >= 0.9 * hits[0]["score"]), best)
        hit = _exact_medicine(driver, best["name"])
        if not hit:
            return None
    return {"input": text.strip(), **hit}


@lru_cache(maxsize=1)
def _condition_lookup():
    said, member = {}, {}
    for label, (words, names) in CONDITION_GROUPS.items():
        for w in words + [label.lower()]:
            said[_norm(w)] = label
        for n in names:
            member.setdefault(n.lower(), label)
    return said, member


def resolve_condition(driver, text):
    """A typed condition becomes one or more Condition keys; everyday words map to a curated group."""
    said, member = _condition_lookup()
    t = _norm(re.sub(r"[^\w' ]+", " ", text))
    if not t:
        return None
    label = said.get(t)
    if not label:
        records, _, _ = driver.execute_query(
            "MATCH (c:Condition) WHERE toLower(c.name) = $t RETURN c.name AS name LIMIT 1", t=t)
        if not records:                                        # fuzzy: prefer conditions that carry warnings
            q = _fulltext(t)
            records, _, _ = driver.execute_query("""
                CALL db.index.fulltext.queryNodes('condition_search', $q, {limit: 25}) YIELD node
                RETURN node.name AS name ORDER BY COUNT { (node)<-[:CONTRAINDICATED_IN|TREATS]-() } DESC, size(node.name)
                LIMIT 1""", q=q) if q else ([], None, None)
        if not records:
            return None
        name = records[0]["name"]
        label = member.get(name.lower())
        if not label:
            k = driver.execute_query("MATCH (c:Condition {name: $n}) RETURN c.key AS k LIMIT 1", n=name).records
            return {"input": text.strip(), "name": name, "keys": [k[0]["k"]], "members": [name]}
    names = CONDITION_GROUPS[label][1]
    records, _, _ = driver.execute_query(
        "MATCH (c:Condition) WHERE c.name IN $names RETURN c.key AS key, c.name AS name", names=names)
    return {"input": text.strip(), "name": label, "keys": [r["key"] for r in records],
            "members": [r["name"] for r in records]}


def _fuzzy(text):
    """Typo-tolerant query: 'crocn advnce' -> 'crocn~ AND advnce~' (numbers must match exactly)."""
    terms = [LUCENE_SPECIAL.sub(r"\\\1", t) for t in re.split(r"[^\w.]+", _norm(text)) if t.strip(".")]
    # pure numbers must match exactly (a dose); "65o" (letter O typed for zero) and words may differ slightly
    return " AND ".join(t if re.fullmatch(r"[\d.]+", t) or len(t) < 3 else t + "~" for t in terms) or None


def did_you_mean(driver, text, kind="medicine", limit=3):
    """Up to `limit` close matches for something that was not recognised."""
    out = []
    if kind == "condition":
        said, _ = _condition_lookup()
        out += [said[m] for m in difflib.get_close_matches(_norm(text), list(said), n=limit, cutoff=0.75)]
        index = "condition_search"
    else:
        names = _resolver().dc_name
        close = difflib.get_close_matches(_norm(text), [n.lower() for n in names.values()], n=limit, cutoff=0.8)
        out += close
        index = "medicine_search"
    q = _fuzzy(text)
    if q:
        out += [r["name"] for r in driver.execute_query(f"""
            CALL db.index.fulltext.queryNodes('{index}', $q, {{limit: 50}}) YIELD node, score
            WHERE NOT coalesce(node.discontinued, false)
            RETURN node.name AS name, max(score) AS score ORDER BY score DESC, size(name) LIMIT $n""",
            q=q, n=limit).records]
    return list(dict.fromkeys(out))[:limit]


# ---------------------------------------------------------------- the checks

def _effect_profile(driver, keys):
    """drug key -> the curated effect groups it belongs to (most dangerous first), by class, name or side effect."""
    side_effects = sorted({s for g in EFFECT_GROUPS for s in g["side_effects"]})
    profile = {}
    for r in driver.execute_query("""
            MATCH (d:Drug) WHERE d.key IN $k
            RETURN d.key AS key, d.name_lower AS name, coalesce(d.atc_classes, []) AS atc,
                   coalesce(d.is_alcohol, false) AS alcohol,
                   [(d)-[:CAUSES]->(s:SideEffect) WHERE s.name IN $se | s.name] AS se""",
            k=keys, se=side_effects).records:
        profile[r["key"]] = [g for g in EFFECT_GROUPS
                             if (r["alcohol"] and g["alcohol"]) or r["name"] in g["names"]
                             or any(c.startswith(p) for c in r["atc"] for p in g["atc"])
                             or set(r["se"]) & set(g["side_effects"])]
    return profile


def _shared_effects(profile, a, b, limit=2):
    """The likely reason two drugs clash: effect groups both belong to, most dangerous first."""
    return [g for g in profile.get(a, []) if g in profile.get(b, [])][:limit]


DAILYMED = "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid="


def _add_quote(quotes, drug, text, set_id):
    """Adds a label sentence unless one already kept says the same (labels repeat a sentence under its heading)."""
    if not any(text in q["text"] or q["text"] in text for q in quotes):
        quotes.append({"drug": drug, "text": text, "url": DAILYMED + set_id})


def _pair_evidence(driver, keys, profile):
    """What independent sources say about each pair in the list, keyed by frozenset({a, b}):
    label: sentences from one drug's FDA label that name the other drug or a group it belongs to;
    reported: side effects reported far more often when both are taken (TWOSIDES, FDA adverse event reports)."""
    label, reported = defaultdict(list), {}
    for r in driver.execute_query("""
            MATCH (a:Drug)-[m:LABEL_MENTIONS]->(b:Drug)
            WHERE a.key IN $k AND b.key IN $k
            MATCH (a)-[:HAS_LABEL]->(l:Label)
            RETURN a.key AS ak, a.name AS a, b.key AS bk, m.sentences AS sentences, l.set_id AS set_id""", k=keys).records:
        for t in r["sentences"]:
            _add_quote(label[frozenset((r["ak"], r["bk"]))], r["a"], t, r["set_id"])
    # sentences naming a group ("NSAIDs", "drugs that prolong the QT interval") the other drug belongs to
    atc = {r["key"]: r["atc"] for r in driver.execute_query(
        "MATCH (d:Drug) WHERE d.key IN $k RETURN d.key AS key, coalesce(d.atc_classes, []) AS atc", k=keys).records}
    for r in driver.execute_query("""
            MATCH (a:Drug)-[:HAS_LABEL]->(l:Label) WHERE a.key IN $k AND l.class_notes <> '[]'
            RETURN a.key AS ak, a.name AS a, l.class_notes AS notes, l.set_id AS set_id""", k=keys).records:
        notes = json.loads(r["notes"])
        for bk in keys:
            if bk == r["ak"]:
                continue
            groups = {g["id"] for g in profile.get(bk, [])}
            hits = [n["text"] for n in notes
                    if any(c in groups or any(x.startswith(c) for x in atc.get(bk, [])) for c in n["classes"])]
            for t in hits[:2]:
                _add_quote(label[frozenset((r["ak"], bk))], r["a"], t, r["set_id"])
    for r in driver.execute_query("""
            MATCH (a:Drug)-[t:REPORTED_TOGETHER]->(b:Drug) WHERE a.key IN $k AND b.key IN $k
            RETURN a.key AS ak, b.key AS bk, t.effects AS effects, t.reports AS reports""", k=keys).records:
        reported[frozenset((r["ak"], r["bk"]))] = [{"effect": e, "reports": n}
                                                    for e, n in zip(r["effects"], r["reports"])][:6]
    return label, reported


BEERS_AGE = 65
BEERS_KIND = {"avoid": "Not advised over 65", "condition": "Not advised with this condition over 65",
              "pair": "Combination to avoid over 65", "count": "Too many of one kind over 65",
              "caution": "Use with care over 65"}


def _beers(driver, keys, owner, names, conditions, found, add):
    """AGS Beers Criteria 2023 for a patient aged 65 or over, as findings (see src/make_beers.py).
    A combination DDInter already flagged gets the Beers advice attached instead of being counted twice, and a
    condition DrugCentral already flagged for that drug is not repeated."""
    rules = {}
    for r in driver.execute_query("""
            MATCH (d:Drug)-[f:FLAGGED_BY]->(g:Guideline) WHERE d.key IN $k
            RETURN g {.*} AS g, d.key AS drug, f.side AS side""", k=keys).records:
        rule = rules.setdefault(r["g"]["key"], {"g": r["g"], "a": [], "b": []})
        rule[r["side"]].append(r["drug"])
    patient = {c["name"] for c in conditions}
    unsafe = {(k, f["title"].split(" should not be used with ")[-1]) for f in found
              if f["kind"] == "Unsafe for patient's condition" for k in f["drugs"]}
    pairs = {frozenset(f["drugs"]): f for f in found if len(f["drugs"]) == 2}
    cite = lambda g: f"Beers 2023 Table {g['table']}, p. {g['page']}: {g['title']}"
    for rule in sorted(rules.values(), key=lambda r: (r["g"]["table"], r["g"]["key"])):
        g, a, b = rule["g"], rule["a"], rule["b"]
        meaning = f"{g['reason']} {g['advice']}".strip()
        kind = BEERS_KIND[g["kind"]]
        if g["kind"] in ("avoid", "caution"):
            for k in a:
                title = (f"{names[k]} is not advised for older adults" if g["kind"] == "avoid" and g["points"] >= 2
                         else f"{names[k]} may not suit an older adult: check if this applies" if g["kind"] == "avoid"
                         else f"{names[k]}: {g['title'][0].lower()}{g['title'][1:]}")
                add(kind, g["points"], title, f"({names[k]})-[FLAGGED_BY]->({cite(g)})", [k], meaning)
        elif g["kind"] == "condition" and g["condition"] in patient:
            for k in a:
                if (k, g["condition"].lower()) not in unsafe:
                    add(kind, g["points"], f"{names[k]}: {g['title'].lower()}",
                        f"({names[k]})-[FLAGGED_BY]->({cite(g)}) <- ({g['condition']})", [k], meaning)
        elif g["kind"] == "pair":
            for x in a:
                for y in b:
                    if x == y or owner[x] & owner[y] or (x > y and x in b and y in a):
                        continue       # same drug, same product, or the same pair seen from the other side
                    existing = pairs.get(frozenset((x, y)))
                    if existing:
                        existing.setdefault("beers", []).append(f"{cite(g)}. {g['advice']}")
                    else:
                        add(kind, g["points"], f"{names[x]} + {names[y]}: {g['title'].lower()}",
                            f"({names[x]})-[FLAGGED_BY]->({cite(g)})<-[FLAGGED_BY]-({names[y]})", [x, y], meaning)
        elif g["kind"] == "count":
            if len({i for k in a for i in owner[k]}) >= g["min_count"]:
                listed = ", ".join(names[k] for k in a)
                add(kind, g["points"], f"{g['title']}: {listed}",
                    f"({listed})-[FLAGGED_BY]->({cite(g)})", sorted(a), meaning)


def _findings(driver, meds, conditions, age=None):
    """All risks inside one medicine list. meds: resolved medicines; conditions: resolved conditions;
    age: the patient's age, when given (the Beers Criteria apply from 65)."""
    owner = defaultdict(set)            # drug key -> indexes of the medicines that contain it
    for i, m in enumerate(meds):
        for d in m["drugs"]:
            owner[d["key"]].add(i)
    keys = list(owner)
    condition_of = {k: c["name"] for c in conditions for k in c["keys"]}
    profile = _effect_profile(driver, keys)
    found = []

    def add(kind, weight, title, path, drugs, meaning, effects=()):
        """meaning: what the finding means to a patient; effects: what can happen and what to watch for."""
        inputs = sorted({i for k in drugs for i in owner[k]})
        found.append({"kind": kind, "weight": weight, "title": title, "path": path, "inputs": inputs, "drugs": drugs,
                      "meaning": meaning, "effects": list(effects)})

    # 1. Direct drug-drug interactions (DDInter)
    for r in driver.execute_query("""
            MATCH (a:Drug)-[x:INTERACTS_WITH]-(b:Drug)
            WHERE a.key IN $k AND b.key IN $k AND a.key < b.key
            RETURN a.key AS ak, a.name AS a, b.key AS bk, b.name AS b, x.severity AS severity""", k=keys).records:
        if owner[r["ak"]] == owner[r["bk"]] and len(owner[r["ak"]]) == 1:
            continue    # both ingredients of the same combination product: intended by the maker
        add("Drug interaction", SEVERITY_WEIGHT[r["severity"]], f"{r['a']} + {r['b']}: {r['severity']} interaction",
            f"({r['a']})-[INTERACTS_WITH {{{r['severity']}}}]-({r['b']})", [r["ak"], r["bk"]],
            SEVERITY_MEANING[r["severity"]], _shared_effects(profile, r["ak"], r["bk"]))

    # 2a. Hidden interactions through a shared liver enzyme (only where no direct record exists)
    for r in driver.execute_query("""
            MATCH (a:Drug)-[x:INHIBITS|INDUCES]->(e:Enzyme)<-[m:METABOLISED_BY]-(b:Drug)
            WHERE a.key IN $k AND b.key IN $k AND a <> b AND NOT (a)-[:INTERACTS_WITH]-(b)
            RETURN a.key AS ak, a.name AS a, type(x) AS effect, x.strength AS strength, e.name AS enzyme,
                   b.key AS bk, b.name AS b, m.sensitivity AS sensitivity""", k=keys).records:
        verb = "blocks" if r["effect"] == "INHIBITS" else "speeds up"
        outcome = "can build up to unsafe levels" if r["effect"] == "INHIBITS" else "can stop working"
        meaning = (f"The liver enzyme {r['enzyme']} breaks down {r['b']}. {r['a']} slows it down, so {r['b']} can build "
                   f"up in the body and its side effects can get stronger." if r["effect"] == "INHIBITS" else
                   f"The liver enzyme {r['enzyme']} breaks down {r['b']}. {r['a']} speeds it up, so {r['b']} is cleared "
                   f"faster and may not work as well as it should.")
        add("Hidden enzyme interaction", ENZYME_WEIGHT[r["strength"]],
            f"{r['a']} {verb} {r['enzyme']}, which clears {r['b']}: {r['b']} {outcome}",
            f"({r['a']})-[{r['effect']} {{{r['strength']}}}]->({r['enzyme']})<-[METABOLISED_BY]-({r['b']})",
            [r["ak"], r["bk"]], meaning)

    # 2b. Duplicate ingredients: the same drug inside two different medicines
    names = {d["key"]: d["name"] for m in meds for d in m["drugs"]}
    for k, idx in owner.items():
        if len(idx) > 1:
            labels = " and ".join(meds[i]["label"] for i in sorted(idx))
            add("Duplicate ingredient", 3, f"{names[k]} is taken twice: in {labels}",
                f"({labels})-[CONTAINS]->({names[k]})", [k],
                f"Both medicines contain {names[k]}, so together they double the dose, which can lead to an overdose. "
                f"Usually only one of them should be taken.")

    # 2c. Two different drugs of the same drug class (ATC level 4) from different medicines
    for r in driver.execute_query("""
            MATCH (a:Drug), (b:Drug) WHERE a.key IN $k AND b.key IN $k AND a.key < b.key
            WITH a, b, [c IN a.atc_classes WHERE c IN b.atc_classes] AS shared WHERE size(shared) > 0
            RETURN a.key AS ak, a.name AS a, b.key AS bk, b.name AS b, shared""", k=keys).records:
        if owner[r["ak"]] & owner[r["bk"]]:
            continue
        add("Same drug class", 1, f"{r['a']} and {r['b']} are the same type of medicine (class {r['shared'][0]})",
            f"({r['a']})-[class {r['shared'][0]}]-({r['b']})", [r["ak"], r["bk"]],
            "Two medicines that do the same job: their effects and side effects add up, and one is often enough.",
            _shared_effects(profile, r["ak"], r["bk"]))

    # 3. Medicines unsafe for the patient's existing conditions (DrugCentral), one warning per drug and condition
    unsafe = defaultdict(list)
    for r in driver.execute_query("""
            MATCH (d:Drug)-[:CONTRAINDICATED_IN]->(c:Condition)
            WHERE d.key IN $k AND c.key IN $c
            RETURN d.key AS dk, d.name AS d, c.key AS ck, c.name AS recorded
            ORDER BY recorded""", k=keys, c=list(condition_of)).records:
        unsafe[(r["dk"], r["d"], condition_of[r["ck"]])].append(r["recorded"])
    for (dk, d, condition), recorded in unsafe.items():
        add("Unsafe for patient's condition", 3, f"{d} should not be used with {condition.lower()}",
            f"({d})-[CONTRAINDICATED_IN]->({' / '.join(recorded[:2])})", [dk],
            f"{d} is recorded as harmful for people with {condition.lower()}: it can make the condition worse "
            f"or cause complications.")

    # 2d. A brand whose combination the Government of India has banned (CDSCO, Section 26A)
    for m in meds:
        for x in m.get("bans", []):
            add("Banned in India", 3, f"{m['label']} is a combination banned in India",
                f"({m['label']})-[BANNED_UNDER]->({x['notification']}, {x['date']})", [d["key"] for d in m["drugs"]],
                f"The Government of India banned the combination {x['combination']} (notification {x['notification']}, "
                f"{x['date']}) because it has no proven benefit or may be unsafe. Ask the doctor for a replacement "
                f"and do not buy it again. Stock bought before the ban may still be in shops or at home.")

    # 4. Prescribing cascades: A causes a side effect that B (also taken) is used to treat.
    #    Only textbook patterns (curated.CASCADE_RULES), and only when the graph has both edges.
    cascades = {}
    for r in driver.execute_query("""
            UNWIND $rules AS rule
            MATCH (a:Drug)-[:CAUSES]->(s:SideEffect)
            WHERE a.key IN $k AND s.name =~ rule.effect_re
              AND (a.name IN rule.cause_names OR any(x IN a.atc_classes WHERE any(p IN rule.cause_atc WHERE x STARTS WITH p)))
            MATCH (b:Drug)-[:TREATS]->(c:Condition)
            WHERE b.key IN $k AND b <> a AND c.name =~ rule.treat_re
              AND NOT EXISTS { (a)-[:TREATS]->(same:Condition) WHERE same.name =~ rule.treat_re }
            WITH a, b, rule, s, c ORDER BY size(c.name), size(s.name)    // simplest names first in the explanation
            RETURN a.key AS ak, a.name AS a, b.key AS bk, b.name AS b, rule.effect AS effect,
                   collect(DISTINCT s.name)[0] AS side_effect, collect(DISTINCT c.name)[0] AS treats""",
            k=keys, rules=CASCADE_RULES).records:
        if owner[r["ak"]] & owner[r["bk"]]:
            continue
        pair = cascades.setdefault((r["ak"], r["bk"]), {"a": r["a"], "b": r["b"], "effects": [], "paths": []})
        pair["effects"].append(r["effect"])
        pair["paths"].append(f"({r['a']})-[CAUSES]->({r['side_effect']}) ~ ({r['treats']})<-[TREATS]-({r['b']})")
    for (ak, bk), c in sorted(cascades.items(), key=lambda kv: -len(kv[1]["effects"]))[:MAX_CASCADES]:
        add("Possible prescribing cascade", 0,
            f"{c['b']} may only be needed because {c['a']} can cause {' and '.join(c['effects'][:3])}",
            c["paths"][0], [ak, bk],
            f"If {c['b']} was started to treat a side effect of {c['a']}, changing {c['a']} may be better "
            f"than adding another medicine.")

    # 6. Older adults: AGS Beers Criteria 2023
    if age is not None and age >= BEERS_AGE:
        _beers(driver, keys, owner, {d["key"]: d["name"] for m in meds for d in m["drugs"]}, conditions, found, add)

    # what the official labels and real-world reports say about each flagged pair
    label, reported = _pair_evidence(driver, keys, profile)
    for f in found:
        pair = frozenset(f["drugs"])
        f["label"] = label.get(pair, [])[:3] if len(pair) == 2 else []
        f["reported"] = reported.get(pair, []) if len(pair) == 2 else []
        f.setdefault("beers", [])
    return found, owner


def _coverage(driver, meds, conditions, findings):
    """Plain-language notes on what the data could not check, so a 0 is never silent."""
    records, _, _ = driver.execute_query("""
        MATCH (d:Drug) WHERE d.key IN $k
        RETURN d.key AS key, d.name AS name,
               COUNT { (d)-[:INTERACTS_WITH]-() } AS interactions, COUNT { (d)-[:CAUSES]->() } AS side_effects,
               COUNT { (d)-[:CONTRAINDICATED_IN]->() } AS condition_rules""",
        k=list({d["key"] for m in meds for d in m["drugs"]}))
    data = {r["key"]: r.data() for r in records}
    notes = []
    for m in meds:
        missing = [d["name"] for d in m["drugs"] if data.get(d["key"], {}).get("interactions", 0) == 0]
        if missing and len(meds) > 1:
            where = f" (in {m['label']})" if m["kind"] == "brand" else ""
            notes.append(f"No interaction data for {', '.join(missing)}{where}: not checked against the other medicines.")
    if conditions:
        missing = sorted({d["name"] for m in meds for d in m["drugs"] if data.get(d["key"], {}).get("condition_rules", 0) == 0})
        if missing:
            notes.append(f"No condition-safety data for {', '.join(missing)}: not checked against the patient's conditions.")
    if len(meds) > 1:
        missing = sorted({d["name"] for m in meds for d in m["drugs"] if data.get(d["key"], {}).get("side_effects", 0) == 0})
        if missing:
            notes.append(f"No side-effect data for {', '.join(missing)}: prescribing cascades could not be checked for them.")
    unknown = sum(1 for f in findings if f["kind"] == "Drug interaction" and "Unknown" in f["title"])
    if unknown:
        notes.append(f"{unknown} interaction{'s have' if unknown > 1 else ' has'} unknown severity in the source data "
                     f"(counted as 1 point{' each' if unknown > 1 else ''}): check {'these' if unknown > 1 else 'it'} with a pharmacist.")
    return notes


def risk_level(findings):
    """A band from the worst finding, not the total, so one serious risk is never diluted by a small sum
    and a long list of minor ones never looks alarming."""
    weights = [f["weight"] for f in findings]
    if 3 in weights:
        return {"level": "Critical", "css": "w3", "why": f"{weights.count(3)} serious finding{'s' * (weights.count(3) > 1)}"}
    if weights.count(2) >= 2:
        return {"level": "High", "css": "w2", "why": f"{weights.count(2)} moderate findings"}
    if 2 in weights:
        return {"level": "Moderate", "css": "w2", "why": "1 moderate finding"}
    if weights:
        return {"level": "Low", "css": "w1", "why": "minor findings only"}
    return {"level": "None found", "css": "good", "why": "no risks found in the data"}


def _alcohol(driver, keys, profile):
    """Medicines that clash with alcohol, with what to do and what can happen."""
    return [{**r.data(), "meaning": ALCOHOL_MEANING[r["severity"]],
             "effects": [g for g in profile.get(r["key"], []) if g["alcohol"]][:2]}
            for r in driver.execute_query("""
        MATCH (d:Drug)-[x:INTERACTS_WITH]-(:Drug {is_alcohol: true}) WHERE d.key IN $k
        RETURN d.key AS key, d.name AS drug, x.severity AS severity
        ORDER BY CASE x.severity WHEN 'Major' THEN 0 WHEN 'Moderate' THEN 1 ELSE 2 END""", k=keys).records]


def _alternatives(driver, drug_key, all_keys, ckeys, limit=3):
    """5. Safer drugs of the same therapeutic group, with a cheaper Jan Aushadhi generic where one exists."""
    return [r.data() for r in driver.execute_query("""
        MATCH (d:Drug {key: $d})
        WITH d, [c IN d.atc_classes | substring(c, 0, 4)] AS groups
        MATCH (alt:Drug)
        WHERE alt.key <> d.key AND NOT alt.key IN $all AND NOT alt.is_alcohol
          AND any(c IN alt.atc_classes WHERE substring(c, 0, 4) IN groups)
          AND NOT EXISTS { (alt)-[:CONTRAINDICATED_IN]->(pc:Condition) WHERE pc.key IN $c }
          AND NOT EXISTS { (alt)-[x:INTERACTS_WITH]-(o:Drug)
                           WHERE o.key IN $all AND o.key <> d.key AND x.severity IN ['Major', 'Moderate'] }
        OPTIONAL MATCH (d)-[:TREATS]->(c:Condition), (alt)-[:TREATS]->(c2:Condition)
          WHERE c = c2 OR any(g IN $groups WHERE c.name IN g AND c2.name IN g)   // same curated condition group
        WITH d, alt, collect(DISTINCT c.name) AS treats_same
        // must treat at least one of the same conditions (when the data lists what d treats)
        WHERE size(treats_same) > 0 OR NOT EXISTS { (d)-[:TREATS]->() }
        WITH alt, treats_same, EXISTS { (:Brand {source: 'india_az', discontinued: false})-[:CONTAINS]->(alt) } AS sold_in_india
        // single-ingredient Jan Aushadhi product only: no combinations, kits or 'and' products
        OPTIONAL MATCH (g:Brand {jan_aushadhi: true})-[:CONTAINS]->(alt)
          WHERE COUNT { (g)-[:CONTAINS]->() } = 1 AND NOT g.name =~ '(?i).*( and |[+]|,| with |combi).*'
        WITH alt, treats_same, sold_in_india, g
        ORDER BY CASE WHEN g.price > 0 THEN 0 ELSE 1 END,
                 CASE WHEN g.name =~ '(?i).*(tablet|capsule).*' THEN 0 ELSE 1 END, g.price
        WITH alt, treats_same, sold_in_india, head(collect(g {.name, .price})) AS generic
        RETURN alt.name AS name, treats_same[..3] AS treats_same, generic
        ORDER BY sold_in_india DESC, size(treats_same) DESC, generic IS NULL, alt.risk_score LIMIT $limit""",
        d=drug_key, all=all_keys, c=ckeys, limit=limit, groups=[names for _, names in CONDITION_GROUPS.values()]).records]


def _lead(text, limit, prefer=None):
    """The opening sentences of a label section, without its heading ("1 INDICATIONS AND USAGE"), starting at the
    first of the first three sentences that contains `prefer` when given ("indicated")."""
    text = re.sub(r"^\s*[\d.]*\s*INDICATIONS\s*(?:AND|&)\s*USAGE\s*", "", re.sub(r"\s+", " ", text or "")).strip()
    text = re.sub(r"\s*(?:\(\s*\d+(?:\.\d+)*\s*\)|\[\s*see [^\]]*\])", "", text, flags=re.I)
    sentences = re.split(r"(?<=[.])\s+(?=[A-Z])", text)
    if prefer:
        first = next((i for i, x in enumerate(sentences[:3]) if prefer in x.lower()), 0)
        sentences = sentences[first:]
    out = ""
    for sentence in sentences:
        if out and len(out) + len(sentence) > limit:
            break
        out += (" " if out else "") + sentence
    return out[:limit + 80]


def about_medicines(driver, meds):
    """Per ingredient: what its official (US FDA) label says it is for, its boxed warning, and a link to the label."""
    keys = list(dict.fromkeys(d["key"] for m in meds for d in m["drugs"]))
    records = {r["key"]: r for r in driver.execute_query("""
        MATCH (d:Drug) WHERE d.key IN $k
        OPTIONAL MATCH (d)-[:HAS_LABEL]->(l:Label)
        RETURN d.key AS key, d.name AS name, l.indications AS indications, l.boxed_warning AS boxed,
               l.set_id AS set_id, l.effective AS effective""", k=keys).records}
    out = []
    for k in keys:
        r = records.get(k)
        if not r:
            continue
        boxed = re.sub(r"\s+", " ", r["boxed"] or "").strip()
        # "WARNING: FETAL TOXICITY • When pregnancy ..." -> title "Fetal toxicity"; other labels have no title
        headline = re.match(r"WARNINGS?:\s*((?:[A-Z0-9,;'()/&-]+\s+)+?)(?=[•\[]|[A-Z][a-z])", boxed)
        out.append({"drug": r["name"], "used_for": _lead(r["indications"], 260, prefer="indicated"),
                    "boxed_title": headline.group(1).strip(" ,;").capitalize() if headline else "",
                    "boxed": _lead((boxed[headline.end():] if headline else boxed).lstrip("• "), 320) if boxed else "",
                    "url": DAILYMED + r["set_id"] if r["set_id"] else "",
                    "effective": f"{r['effective'][:4]}-{r['effective'][4:6]}" if r["effective"] else ""})
    return out


PACK_UNITS = re.compile(r"\bof (\d+) (tablet|capsule|sachet|strip)s?\b", re.I)


def _unit_price(price, pack):
    """'strip of 15 tablets', 34.27 -> (2.28, 'tablet'); None when the pack does not say how many units."""
    m = PACK_UNITS.search(pack or "")
    return (round(price / int(m.group(1)), 2), m.group(2).lower()) if m and price and int(m.group(1)) else None


def about_brands(driver, meds, substitutes=3):
    """Per Indian brand in the list: what it is used for, common side effects and whether it is habit forming
    (250k Indian medicines dataset), and same-ingredient substitutes still sold, cheapest per tablet first.
    Pack sizes differ, so prices are compared per tablet/capsule, or per pack only when the packs are the same."""
    keys = [m["brand_key"] for m in meds if m.get("brand_key")]
    out = []
    for r in driver.execute_query("""
            UNWIND $k AS key
            MATCH (b:Brand {key: key})
            CALL (b) {
              OPTIONAL MATCH (b)-[x:SUBSTITUTE]->(s:Brand)
              WHERE NOT s.discontinued AND s.price > 0 AND NOT EXISTS { (s)-[:BANNED_UNDER]->() }
              RETURN collect(s {.name, .price, .pack, .manufacturer, same_strength: x.same_strength}) AS subs
            }
            RETURN b.name AS name, b.price AS price, b.pack AS pack, b.discontinued AS discontinued,
                   coalesce(b.uses, []) AS uses, coalesce(b.side_effects, []) AS side_effects,
                   coalesce(b.habit_forming, false) AS habit_forming, b.therapeutic_class AS therapeutic_class, subs""",
            k=keys).records:
        own = _unit_price(r["price"], r["pack"])
        subs = []
        for s in r["subs"]:
            unit = _unit_price(s["price"], s["pack"])
            comparable = unit and own and unit[1] == own[1] and s["same_strength"]
            subs.append({**s, "unit": unit, "cheaper": (unit[0] < own[0]) if comparable else
                         (s["same_strength"] and s["pack"] == r["pack"] and bool(r["price"]) and s["price"] < r["price"])})
        subs.sort(key=lambda s: (not s["same_strength"], s["unit"] is None, s["unit"][0] if s["unit"] else s["price"]))
        out.append({**r.data(), "unit": own, "uses": r["uses"][:3], "side_effects": r["side_effects"][:8],
                    "subs": subs[:substitutes]})
    return out


def top_risk_medicines(driver, limit=20):
    """6. Medicines that interact with the most others, weighted by severity (GDS degree centrality)."""
    return [r.data() for r in driver.execute_query("""
        MATCH (d:Drug) WHERE d.risk_score IS NOT NULL AND NOT d.is_alcohol
        RETURN d.name AS name, toInteger(d.risk_score) AS risk_score,
               COUNT { (d)-[:INTERACTS_WITH {severity: 'Major'}]-() } AS major
        ORDER BY risk_score DESC LIMIT $limit""", limit=limit).records]


def analyse(driver, medicine_texts, condition_texts, new_medicine=None, age=None):
    meds, conditions, unknown = [], [], []

    def not_found(text, kind, field):
        unknown.append({"text": text.strip(), "field": field, "suggestions": did_you_mean(driver, text, kind)})

    for t in medicine_texts:
        m = resolve_medicine(driver, t)
        (meds.append(m) if m else not_found(t, "medicine", "medicines"))
    for t in condition_texts:
        c = resolve_condition(driver, t)
        (conditions.append(c) if c else not_found(t, "condition", "conditions"))

    findings, owner = _findings(driver, meds, conditions, age)
    cascades = [f for f in findings if f["kind"] == "Possible prescribing cascade"]
    cautions = [f for f in findings if f["kind"] == BEERS_KIND["caution"]]
    findings = sorted((f for f in findings if f["weight"] > 0), key=lambda f: -f["weight"])
    score = sum(f["weight"] for f in findings)
    keys, ckeys = list(owner), [k for c in conditions for k in c["keys"]]

    # Feature 5b: which one medicine, if stopped, removes the most risk
    removal = [(sum(f["weight"] for f in findings if i in f["inputs"]), m["label"]) for i, m in enumerate(meds)]
    deprescribe = max(removal) if removal and max(removal)[0] > 0 else None

    # Feature 5a: safer options for drugs involved in serious findings
    names = {d["key"]: d["name"] for m in meds for d in m["drugs"]}
    flagged = list(dict.fromkeys(k for f in findings if f["weight"] >= 2 for k in f["drugs"]))
    alternatives = [{"drug": names[k], "options": _alternatives(driver, k, keys, ckeys)} for k in flagged[:4]]

    # Feature 1b: what-if check before adding a new medicine
    what_if = None
    if new_medicine and new_medicine.strip():
        extra = resolve_medicine(driver, new_medicine)
        if extra:
            new_findings, _ = _findings(driver, meds + [extra], conditions, age)
            new_score = sum(f["weight"] for f in new_findings)
            existing = {f["title"] for f in findings}
            what_if = {"medicine": extra["label"], "before": score, "after": new_score,
                       "level_before": risk_level(findings),
                       "level_after": risk_level([f for f in new_findings if f["weight"] > 0]),
                       "new": sorted((f for f in new_findings if f["weight"] > 0 and f["title"] not in existing),
                                     key=lambda f: -f["weight"])}
        else:
            not_found(new_medicine, "medicine", "new_medicine")

    # Effects that add up across the whole list: 3+ different medicines doing the same risky thing,
    # which no pairwise check can show
    profile = _effect_profile(driver, keys)
    stacked = []
    for g in (g for g in EFFECT_GROUPS if g["stack"]):
        members = [k for k in keys if g in profile.get(k, [])]
        if len({i for k in members for i in owner[k]}) >= 3:
            stacked.append({"group": g, "drugs": [names[k] for k in members]})

    risk = {r["name"]: r["risk_score"] for r in driver.execute_query(
        "MATCH (d:Drug) WHERE d.key IN $k RETURN d.name AS name, toInteger(d.risk_score) AS risk_score",
        k=keys).records}
    return {"medicines": meds, "conditions": conditions, "unknown": unknown, "score": score, "level": risk_level(findings),
            "findings": findings, "cascades": cascades, "coverage": _coverage(driver, meds, conditions, findings), "alcohol": _alcohol(driver, keys, profile), "stacked": stacked, "about": about_medicines(driver, meds),
            "brands": about_brands(driver, meds), "alternatives": alternatives,
            "deprescribe": deprescribe, "what_if": what_if, "drug_risk": risk, "age": age, "cautions": cautions}
