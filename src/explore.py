"""Queries behind the graph explorer page (/graph): a checked medicine list as a risk graph, browse any node and its
neighbours, see the schema, and the whole drug interaction network. Everything returns plain data that app.py sends to the browser as JSON.

Nodes are addressed by Neo4j's elementId, so an id is only valid until the database is rebuilt. Relationship types
cannot be Cypher parameters, so any type coming from the browser is checked against RELATIONSHIPS first.
"""
from collections import defaultdict
from functools import lru_cache

from curated import CONDITION_GROUPS
from queries import _fulltext

# every relationship type in the graph, as the browser may ask for them
RELATIONSHIPS = ["CONTAINS", "INTERACTS_WITH", "INHIBITS", "INDUCES", "METABOLISED_BY", "TREATS",
                 "CONTRAINDICATED_IN", "CAUSES", "HAS_LABEL", "LABEL_MENTIONS", "REPORTED_TOGETHER", "BANNED_UNDER",
                 "SUBSTITUTE", "FLAGGED_BY"]
# the name shown for a node, whatever its label
NAME = "coalesce(n.name, n.title, n.notification, 'US label: ' + n.substance, n.key)"
MAX_LIMIT = 100
# the property that identifies a node of each label (unique constraint or index)
KEY_PROPERTY = {"Drug": "key", "Brand": "key", "Condition": "key", "SideEffect": "key", "Enzyme": "name",
                "Ban": "key", "Guideline": "key"}


def _value(v):
    """Neo4j values the browser cannot read as JSON (dates) become text."""
    if isinstance(v, list):
        return [_value(x) for x in v]
    return v if v is None or isinstance(v, (str, int, float, bool)) else str(v)


def _node(n, name):
    return {"id": n.element_id, "label": next(iter(n.labels), ""), "name": name,
            "props": {k: _value(v) for k, v in n.items()}}


def _edge(r):
    return {"id": r.element_id, "type": r.type, "source": r.start_node.element_id, "target": r.end_node.element_id,
            "props": {k: _value(v) for k, v in r.items()}}


@lru_cache(maxsize=1)
def schema(driver):
    """The node types and relationship types, with counts (fast: Neo4j keeps these counts)."""
    records, _, _ = driver.execute_query("CALL db.schema.visualization() YIELD nodes, relationships "
                                         "RETURN nodes, relationships")
    nodes, links = records[0]["nodes"], records[0]["relationships"]
    labels = sorted({next(iter(n.labels)) for n in nodes})
    counts = {label: driver.execute_query(f"MATCH (n:`{label}`) RETURN count(n) AS c").records[0]["c"]
              for label in labels}
    edges = []
    for r in links:
        if r.type in RELATIONSHIPS:
            c = driver.execute_query(f"MATCH ()-[r:`{r.type}`]->() RETURN count(r) AS c").records[0]["c"]
            edges.append({"type": r.type, "from": next(iter(r.start_node.labels)),
                          "to": next(iter(r.end_node.labels)), "count": c})
    return {"labels": [{"label": label, "count": counts[label]} for label in labels], "relationships": edges}


def search(driver, text, limit=15):
    """Medicines, brands, conditions, side effects, enzymes and guidelines whose name matches."""
    q = _fulltext(text)
    if not q:
        return []
    found = []
    for index in ("medicine_search", "condition_search"):
        found += [_node(r["n"], r["name"]) for r in driver.execute_query(f"""
            CALL db.index.fulltext.queryNodes('{index}', $q, {{limit: $limit}}) YIELD node AS n
            WHERE NOT coalesce(n.discontinued, false)
            RETURN n, {NAME} AS name""", q=q, limit=limit).records]
    found += [_node(r["n"], r["name"]) for r in driver.execute_query(f"""
        MATCH (n) WHERE (n:SideEffect OR n:Enzyme OR n:Guideline OR n:Ban)
          AND toLower({NAME}) CONTAINS toLower($t)
        RETURN n, {NAME} AS name ORDER BY size({NAME}) LIMIT $limit""", t=text.strip(), limit=limit).records]
    return found[:limit * 2]


def node(driver, element_id):
    """One node with all its properties and how many relationships of each type it has."""
    records, _, _ = driver.execute_query(f"""
        MATCH (n) WHERE elementId(n) = $id
        CALL (n) {{ MATCH (n)-[r]-() RETURN type(r) AS type, count(*) AS count ORDER BY type }}
        RETURN n, {NAME} AS name, collect({{type: type, count: count}}) AS degree""", id=element_id)
    if not records:
        return None
    r = records[0]
    return {**_node(r["n"], r["name"]), "degree": r["degree"]}


def expand(driver, element_id, types=None, limit=25):
    """A node's neighbours, at most `limit` per relationship type: the most serious or most relevant first
    (severe interactions, medicines still sold, cheapest substitutes)."""
    types = [t for t in (types or RELATIONSHIPS) if t in RELATIONSHIPS]
    limit = max(1, min(int(limit), MAX_LIMIT))
    records, _, _ = driver.execute_query(f"""
        MATCH (n) WHERE elementId(n) = $id
        UNWIND $types AS t
        CALL (n, t) {{
          MATCH (n)-[r]-(m) WHERE type(r) = t
          WITH r, m ORDER BY coalesce(r.weight, 0) DESC, coalesce(m.discontinued, false),
                             coalesce(m.risk_score, 0) DESC, coalesce(m.price, 0), coalesce(m.name, m.title, '')
          RETURN collect({{r: r, m: m}}) AS found
        }}
        WITH t, found WHERE size(found) > 0
        UNWIND found[..$limit] AS pair
        WITH t, size(found) AS total, pair.r AS r, pair.m AS n
        RETURN t, total, r, n, {NAME} AS name""", id=element_id, types=types, limit=limit)
    nodes, edges, totals = {}, [], {}
    for r in records:
        nodes[r["n"].element_id] = _node(r["n"], r["name"])
        edges.append(_edge(r["r"]))
        totals[r["t"]] = r["total"]
    return {"nodes": list(nodes.values()), "edges": edges, "totals": totals}


def risk_graph(driver, result):
    """A checked medicine list (queries.analyse) as a graph that holds only what produced its warnings: each
    finding's path (finding["links"]), plus the brand -> ingredient links so every medicine is shown. Nothing else
    is added, so the graph and the list of warnings always agree.

    Node ids are "Label:key". Real nodes also carry their Neo4j elementId ("eid") so the details panel can load
    them; derived nodes (a drug class, "same problem") are worked out by the app and have none.
    Each node and edge lists the findings it belongs to, by position in the returned "findings"."""
    nodes, edges = {}, {}

    def node(n, finding=None):
        nid = f"{n['label']}:{n['key']}"
        item = nodes.setdefault(nid, {"id": nid, "label": n["label"], "key": n["key"], "name": n["name"],
                                      "derived": n["derived"], "findings": []})
        if finding is not None and finding not in item["findings"]:
            item["findings"].append(finding)
        return nid

    def edge(source, type_, target, derived=False, props=None, finding=None):
        eid = f"{source}|{type_}|{target}"
        item = edges.setdefault(eid, {"id": eid, "source": source, "target": target, "type": type_,
                                      "derived": derived, "props": props or {}, "findings": []})
        if finding is not None and finding not in item["findings"]:
            item["findings"].append(finding)
        return eid

    for m in result["medicines"]:                                  # every medicine, even without a warning
        drugs = [node({"label": "Drug", "key": d["key"], "name": d["name"], "derived": False}) for d in m["drugs"]]
        if m.get("brand_key"):
            brand = node({"label": "Brand", "key": m["brand_key"], "name": m["label"], "derived": False})
            for d in drugs:
                edge(brand, "CONTAINS", d)

    findings = []
    for i, f in enumerate(result["findings"] + result["cascades"]):
        node_ids, edge_ids = set(), set()
        for link in f["links"]:
            a, b = node(link["source"], i), node(link["target"], i)
            edge_ids.add(edge(a, link["type"], b, link["derived"], link["props"], i))
            node_ids |= {a, b}
        for k in f["drugs"]:                                       # a one-drug finding still marks its drug
            node_ids.add(node({"label": "Drug", "key": k, "name": "", "derived": False}, i))
        findings.append({"index": i, "title": f["title"], "kind": f["kind"], "weight": f["weight"],
                         "meaning": f["meaning"], "path": f["path"], "effects": f["effects"],
                         "label": f.get("label", []), "reported": f.get("reported", []), "beers": f.get("beers", []),
                         "nodes": sorted(node_ids), "edges": sorted(edge_ids)})

    # the database ids of the real nodes, so clicking one can load all its details (one indexed lookup per label;
    # the label comes from this fixed list, never from the browser)
    by_label = defaultdict(list)
    for n in nodes.values():
        if not n["derived"]:
            by_label[n["label"]].append(n["key"])
    for label, keys in by_label.items():
        if label not in KEY_PROPERTY:
            continue
        prop = KEY_PROPERTY[label]
        for r in driver.execute_query(f"MATCH (x:`{label}`) WHERE x.{prop} IN $keys "
                                      f"RETURN x.{prop} AS key, elementId(x) AS eid", keys=keys).records:
            nodes[f"{label}:{r['key']}"]["eid"] = r["eid"]
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "findings": findings,
            "level": result["level"], "score": result["score"]}


# ---------------------------------------------------------------- profiles: one node and its meaningful neighbours

# how many neighbours a profile box shows; the box always says how many there are in all
PROFILE_LIMIT = {"interactions": 10, "conditions": 8, "enzyme": 30}
STRENGTH_ORDER = "CASE coalesce(r.strength, r.sensitivity) WHEN 'strong' THEN 0 WHEN 'sensitive' THEN 0 " \
                 "WHEN 'moderate' THEN 1 ELSE 2 END"


@lru_cache(maxsize=1)
def _everyday_conditions():
    """Condition names behind the everyday words people type ("sugar", "BP"): shown first in a profile."""
    return sorted({n for _, names in CONDITION_GROUPS.values() for n in names})


def profile(driver, label, key):
    """A drug's profile or an enzyme's hub, or None for other node types (or an unknown node)."""
    if label == "Drug":
        return _drug_profile(driver, key)
    if label == "Enzyme":
        return _enzyme_hub(driver, key)
    return None


def _boxes():
    """Collects a profile: the centre, then boxes of neighbours. Box nodes are Cytoscape compound parents."""
    out = {"nodes": [], "edges": [], "boxes": []}

    def box(box_id, title, records, total, note=""):
        if not records:
            return
        out["boxes"].append({"id": box_id, "title": title, "shown": len(records), "total": total, "note": note})
        out["nodes"].append({"id": box_id, "label": "Box", "name": f"{title} · {len(records)} of {total}"
                             if total > len(records) else f"{title} · {total}", "props": {}, "box": True})
        for r in records:
            n = _node(r["n"], r["name"])
            out["nodes"].append({**n, "parent": box_id, "eid": n["id"], "id": f"{box_id}/{n['id']}"})
            e = _edge(r["r"])
            e["source"], e["target"] = (out["center"]["id"], f"{box_id}/{n['id']}") if e["source"] == out["center"]["id"] \
                else (f"{box_id}/{n['id']}", out["center"]["id"])
            e["id"] = f"{box_id}/{e['id']}"
            out["edges"].append(e)
    return out, box


def _drug_profile(driver, key):
    records = driver.execute_query(f"MATCH (n:Drug {{key: $k}}) RETURN n, {NAME} AS name", k=key).records
    if not records:
        return None
    out, box = _boxes()
    center = _node(records[0]["n"], records[0]["name"])
    out["center"] = {**center, "eid": center["id"]}
    out["nodes"].append(out["center"])
    q = lambda cypher, **kw: driver.execute_query(cypher, k=key, **kw).records
    count = lambda cypher: driver.execute_query(cypher, k=key).records[0]["c"]

    box("enzymes", "Liver enzymes", q(f"""
        MATCH (d:Drug {{key: $k}})-[r:INHIBITS|INDUCES|METABOLISED_BY]->(n:Enzyme)
        RETURN r, n, {NAME} AS name ORDER BY n.name"""),
        count("MATCH (:Drug {key: $k})-[r:INHIBITS|INDUCES|METABOLISED_BY]->() RETURN count(r) AS c"))

    severities = {r["s"]: r["c"] for r in q("""
        MATCH (:Drug {key: $k})-[x:INTERACTS_WITH]-(o:Drug) WHERE NOT o.is_alcohol
        RETURN x.severity AS s, count(*) AS c""")}
    box("interactions", "Serious interactions", q(f"""
        MATCH (d:Drug {{key: $k}})-[r:INTERACTS_WITH {{severity: 'Major'}}]-(n:Drug) WHERE NOT n.is_alcohol
        RETURN r, n, {NAME} AS name
        ORDER BY EXISTS {{ (:Brand {{source: 'india_az', discontinued: false}})-[:CONTAINS]->(n) }} DESC,
                 n.risk_score DESC LIMIT $limit""", limit=PROFILE_LIMIT["interactions"]),
        severities.get("Major", 0),
        note=", ".join(f"{severities[s]:,} {s.lower()}" for s in ("Major", "Moderate", "Minor", "Unknown")
                       if severities.get(s)) + " interactions in all; the box shows Major ones, "
                                               "medicines sold in India first")

    for rel, box_id, title in (("TREATS", "treats", "Treats"), ("CONTRAINDICATED_IN", "unsafe", "Unsafe with")):
        box(box_id, title, q(f"""
            MATCH (d:Drug {{key: $k}})-[r:{rel}]->(n:Condition)
            RETURN r, n, {NAME} AS name ORDER BY n.name IN $everyday DESC, size(n.name) LIMIT $limit""",
            everyday=_everyday_conditions(), limit=PROFILE_LIMIT["conditions"]),
            count(f"MATCH (:Drug {{key: $k}})-[r:{rel}]->() RETURN count(r) AS c"),
            note="everyday conditions first")

    box("beers", "Older adults (Beers 65+)", q(f"""
        MATCH (d:Drug {{key: $k}})-[r:FLAGGED_BY]->(n:Guideline) RETURN r, n, {NAME} AS name ORDER BY n.table, n.key"""),
        count("MATCH (:Drug {key: $k})-[r:FLAGGED_BY]->() RETURN count(r) AS c"))
    box("label", "Official US label", q(f"""
        MATCH (d:Drug {{key: $k}})-[r:HAS_LABEL]->(n:Label) RETURN r, n, {NAME} AS name"""), 1)

    # Indian brands: one summary, not thousands of nodes
    brands = q("""
        MATCH (:Drug {key: $k})<-[:CONTAINS]-(b:Brand)
        RETURN count(b) AS brands, count(CASE WHEN NOT b.discontinued THEN 1 END) AS sold,
               count(CASE WHEN EXISTS { (b)-[:BANNED_UNDER]->() } THEN 1 END) AS banned,
               count(CASE WHEN b.jan_aushadhi THEN 1 END) AS jan_aushadhi""")[0].data()
    if brands["brands"]:
        out["nodes"].append({"id": "brands", "label": "Brand", "derived": True,
                             "name": f"{brands['brands']:,} brands" + (f" · {brands['banned']} banned" if brands["banned"] else ""),
                             "props": {"brands in the data": brands["brands"], "still sold (2022)": brands["sold"],
                                       "banned combinations": brands["banned"],
                                       "Jan Aushadhi generics": brands["jan_aushadhi"]}})
        out["edges"].append({"id": "brands-edge", "type": "CONTAINS", "source": "brands", "target": out["center"]["id"],
                             "props": {}, "derived": True})
    return out


def _enzyme_hub(driver, name):
    records = driver.execute_query(f"MATCH (n:Enzyme {{name: $k}}) RETURN n, {NAME} AS name", k=name).records
    if not records:
        return None
    out, box = _boxes()
    center = _node(records[0]["n"], records[0]["name"])
    out["center"] = {**center, "eid": center["id"]}
    out["nodes"].append(out["center"])
    for rel, box_id, title in (("INHIBITS", "blocked", "Blocked by"), ("INDUCES", "sped", "Sped up by"),
                               ("METABOLISED_BY", "clears", "Clears")):
        records = driver.execute_query(f"""
            MATCH (n:Drug)-[r:{rel}]->(:Enzyme {{name: $k}})
            RETURN r, n, {NAME} AS name ORDER BY {STRENGTH_ORDER}, n.name LIMIT $limit""",
            k=name, limit=PROFILE_LIMIT["enzyme"]).records
        total = driver.execute_query(f"MATCH (:Drug)-[r:{rel}]->(:Enzyme {{name: $k}}) RETURN count(r) AS c",
                                     k=name).records[0]["c"]
        box(box_id, title, records, total, note="strongest first")
    pairs = driver.execute_query("""
        MATCH (a:Drug)-[:INHIBITS|INDUCES]->(:Enzyme {name: $k})<-[:METABOLISED_BY]-(b:Drug) WHERE a <> b
        RETURN count(*) AS pairs, count(CASE WHEN NOT (a)-[:INTERACTS_WITH]-(b) THEN 1 END) AS hidden""",
        k=name).records[0]
    out["pairs"], out["hidden"] = pairs["pairs"], pairs["hidden"]
    return out


@lru_cache(maxsize=2)
def drug_network(driver, severity="Major"):
    """Every drug with at least one interaction of this severity, and those interactions: the whole-picture view.
    Kept small for the browser: interactions are pairs of positions in the node list, not ids."""
    severity = severity if severity in ("Major", "Moderate") else "Major"
    pairs = [(r["a"], r["b"]) for r in driver.execute_query("""
        MATCH (a:Drug)-[x:INTERACTS_WITH {severity: $s}]->(b:Drug) WHERE NOT a.is_alcohol AND NOT b.is_alcohol
        RETURN elementId(a) AS a, elementId(b) AS b""", s=severity).records]
    nodes = [r.data() for r in driver.execute_query("""
        MATCH (d:Drug) WHERE elementId(d) IN $ids
        RETURN elementId(d) AS id, d.name AS name, toInteger(coalesce(d.risk_score, 0)) AS risk,
               coalesce(left(d.atc_classes[0], 1), '?') AS group ORDER BY d.name""",
        ids=list({i for pair in pairs for i in pair})).records]
    position = {n["id"]: i for i, n in enumerate(nodes)}
    return {"severity": severity, "nodes": nodes, "edges": [(position[a], position[b]) for a, b in pairs]}
