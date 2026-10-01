"""Load the clean CSVs from build/ into Neo4j, then rank drugs with Graph Data Science.

Wipes the database first, so it is safe to run again after changing the data.
Run:  python src/load_graph.py        (run src/prepare_data.py first)
"""
import csv
import json
import time

from config import ROOT, get_driver

BUILD = ROOT / "build"
BATCH = 10_000
SEVERITY_WEIGHT = {"Major": 3, "Moderate": 2, "Minor": 1, "Unknown": 1}

SCHEMA = [
    "CREATE CONSTRAINT drug_key IF NOT EXISTS FOR (d:Drug) REQUIRE d.key IS UNIQUE",
    "CREATE CONSTRAINT brand_key IF NOT EXISTS FOR (b:Brand) REQUIRE b.key IS UNIQUE",
    "CREATE CONSTRAINT condition_key IF NOT EXISTS FOR (c:Condition) REQUIRE c.key IS UNIQUE",
    "CREATE CONSTRAINT side_effect_key IF NOT EXISTS FOR (s:SideEffect) REQUIRE s.key IS UNIQUE",
    "CREATE CONSTRAINT enzyme_name IF NOT EXISTS FOR (e:Enzyme) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT ban_key IF NOT EXISTS FOR (x:Ban) REQUIRE x.key IS UNIQUE",
    "CREATE CONSTRAINT guideline_key IF NOT EXISTS FOR (g:Guideline) REQUIRE g.key IS UNIQUE",
    "CREATE CONSTRAINT drug_class_code IF NOT EXISTS FOR (c:DrugClass) REQUIRE c.code IS UNIQUE",
    "CREATE INDEX condition_cui IF NOT EXISTS FOR (c:Condition) ON (c.cui)",
    "CREATE INDEX side_effect_cui IF NOT EXISTS FOR (s:SideEffect) ON (s.cui)",
    "CREATE INDEX drug_name IF NOT EXISTS FOR (d:Drug) ON (d.name_lower)",
    "CREATE INDEX brand_name IF NOT EXISTS FOR (b:Brand) ON (b.name_lower)",
    "CREATE FULLTEXT INDEX medicine_search IF NOT EXISTS FOR (n:Drug|Brand) ON EACH [n.name]",
    "CREATE FULLTEXT INDEX condition_search IF NOT EXISTS FOR (n:Condition) ON EACH [n.name]",
]

# (csv file, Cypher that consumes a batch of its rows as $rows, row converter)
STEPS = [
    ("drugs.csv", """
        UNWIND $rows AS r
        CREATE (:Drug {key: r.key, name: r.name, name_lower: toLower(r.name), dc_id: r.dc_id,
                       atc_classes: r.atc_classes, atc_class_names: r.atc_class_names,
                       sources: r.sources, is_alcohol: r.is_alcohol})""",
     lambda r: {**r, "atc_classes": [c for c in r["atc_classes"].split(";") if c],
                "atc_class_names": [c for c in r["atc_class_names"].split(";") if c],
                "sources": r["sources"].split(";"), "is_alcohol": r["is_alcohol"] == "True"}),
    ("drug_classes.csv", """
        UNWIND $rows AS r
        CREATE (:DrugClass {code: r.code, level: r.level, name: r.name, atc_name: r.atc_name})""",
     lambda r: {**r, "level": int(r["level"])}),
    ("drug_classes.csv", """
        UNWIND $rows AS r
        MATCH (c:DrugClass {code: r.code}), (p:DrugClass {code: r.parent}) CREATE (c)-[:PART_OF]->(p)""", None),
    ("belongs_to.csv", """
        UNWIND $rows AS r
        MATCH (d:Drug {key: r.drug}), (c:DrugClass {code: r.class}) CREATE (d)-[:BELONGS_TO]->(c)""", None),
    ("enzymes.csv", "UNWIND $rows AS r CREATE (:Enzyme {name: r.name})", None),
    ("conditions.csv", "UNWIND $rows AS r CREATE (:Condition {key: r.key, name: r.name, cui: r.cui})", None),
    ("side_effects.csv", "UNWIND $rows AS r CREATE (:SideEffect {key: r.key, name: r.name, cui: r.cui})", None),
    ("brands.csv", """
        UNWIND $rows AS r
        CREATE (:Brand {key: r.key, name: r.name, name_lower: toLower(r.name), manufacturer: r.manufacturer,
                        price: r.price, discontinued: r.discontinued, type: r.type, pack: r.pack,
                        source: r.source, jan_aushadhi: r.jan_aushadhi, group: r.group,
                        composition_incomplete: r.composition_incomplete})""",
     lambda r: {**r, "price": float(r["price"]) if r["price"] else None,
                "discontinued": r["discontinued"] == "True", "jan_aushadhi": r["jan_aushadhi"] == "True",
                "composition_incomplete": r["composition_incomplete"] == "True"}),
    ("interactions.csv", """
        UNWIND $rows AS r
        MATCH (a:Drug {key: r.a}), (b:Drug {key: r.b})
        CREATE (a)-[:INTERACTS_WITH {severity: r.severity, weight: r.weight}]->(b)""",
     lambda r: {**r, "weight": SEVERITY_WEIGHT[r["severity"]]}),
    ("enzyme_effects.csv", """
        UNWIND $rows AS r
        MATCH (d:Drug {key: r.drug}), (e:Enzyme {name: r.enzyme})
        CALL (d, e, r) {
          WITH d, e, r WHERE r.rel = 'INHIBITS'       CREATE (d)-[:INHIBITS {strength: r.strength}]->(e)
          UNION
          WITH d, e, r WHERE r.rel = 'INDUCES'        CREATE (d)-[:INDUCES {strength: r.strength}]->(e)
          UNION
          WITH d, e, r WHERE r.rel = 'METABOLISED_BY' CREATE (d)-[:METABOLISED_BY {sensitivity: r.strength}]->(e)
        }""", None),
    ("treats.csv", """
        UNWIND $rows AS r
        MATCH (d:Drug {key: r.drug}), (c:Condition {key: r.condition}) CREATE (d)-[:TREATS]->(c)""", None),
    ("contraindicated_in.csv", """
        UNWIND $rows AS r
        MATCH (d:Drug {key: r.drug}), (c:Condition {key: r.condition}) CREATE (d)-[:CONTRAINDICATED_IN]->(c)""", None),
    ("causes.csv", """
        UNWIND $rows AS r
        MATCH (d:Drug {key: r.drug}), (s:SideEffect {key: r.side_effect}) CREATE (d)-[:CAUSES]->(s)""", None),
    ("contains.csv", """
        UNWIND $rows AS r
        MATCH (b:Brand {key: r.brand}), (d:Drug {key: r.drug}) MERGE (b)-[c:CONTAINS]->(d)
        SET c.strength = r.strength""", None),
    ("bans.csv", """
        UNWIND $rows AS r
        CREATE (:Ban {key: r.key, combination: r.combination, notification: r.notification, date: date(r.date),
                      list: r.list, form: r.form})""", None),
    ("banned_under.csv", """
        UNWIND $rows AS r
        MATCH (b:Brand {key: r.brand}), (x:Ban {key: r.ban}) CREATE (b)-[:BANNED_UNDER]->(x)""", None),
    ("brand_details.csv", """
        UNWIND $rows AS r
        MATCH (b:Brand {key: r.brand})
        SET b.uses = r.uses, b.side_effects = r.side_effects, b.habit_forming = r.habit_forming,
            b.therapeutic_class = r.therapeutic_class, b.action_class = r.action_class,
            b.chemical_class = r.chemical_class""",
     lambda r: {**r, "uses": [u for u in r["uses"].split("|") if u],
                "side_effects": [e for e in r["side_effects"].split("|") if e],
                "habit_forming": r["habit_forming"] == "True"}),
    ("substitutes.csv", """
        UNWIND $rows AS r
        MATCH (b:Brand {key: r.brand}), (s:Brand {key: r.substitute})
        CREATE (b)-[:SUBSTITUTE {same_strength: r.same_strength}]->(s)""",
     lambda r: {**r, "same_strength": r["same_strength"] == "True"}),
    ("guidelines.csv", """
        UNWIND $rows AS r
        CREATE (:Guideline {key: r.key, source: 'AGS Beers Criteria 2023', table: r.table, page: r.page,
                            kind: r.kind, title: r.title, advice: r.advice, reason: r.reason, evidence: r.evidence,
                            points: r.points, condition: r.condition, min_count: r.min_count})""",
     lambda r: {**r, "table": int(r["table"]), "page": int(r["page"]), "points": int(r["points"]),
                "min_count": int(r["min_count"]) if r["min_count"] else None}),
    ("flagged_by.csv", """
        UNWIND $rows AS r
        MATCH (d:Drug {key: r.drug}), (g:Guideline {key: r.guideline}) CREATE (d)-[:FLAGGED_BY {side: r.side}]->(g)""",
     None),
    ("labels.csv", """
        UNWIND $rows AS r
        MATCH (d:Drug {key: r.drug})
        CREATE (d)-[:HAS_LABEL]->(:Label {set_id: r.set_id, effective: r.effective, substance: r.substance,
                                          brand: r.brand, boxed_warning: r.boxed_warning, indications: r.indications,
                                          contraindications: r.contraindications, patient_info: r.patient_info,
                                          geriatric_use: r.geriatric_use, pregnancy: r.pregnancy,
                                          class_notes: r.class_notes})""", None),
    ("label_mentions.csv", """
        UNWIND $rows AS r
        MATCH (a:Drug {key: r.a}), (b:Drug {key: r.b}) CREATE (a)-[:LABEL_MENTIONS {sentences: r.sentences}]->(b)""",
     lambda r: {**r, "sentences": json.loads(r["sentences"])}),
    ("reported_together.csv", """
        UNWIND $rows AS r
        MATCH (a:Drug {key: r.a}), (b:Drug {key: r.b})
        CREATE (a)-[:REPORTED_TOGETHER {effects: r.effects, reports: r.reports, prr: r.prr}]->(b)""",
     lambda r: {**r, "effects": json.loads(r["effects"]), "reports": json.loads(r["reports"]), "prr": json.loads(r["prr"])}),
]


def load():
    driver = get_driver()
    driver.verify_connectivity()
    with driver.session() as session:
        print("Clearing the database ...")
        # relationships first, in small batches: one drug can have hundreds, which overflows a single transaction
        session.run("MATCH ()-[r]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 20000 ROWS").consume()
        session.run("MATCH (n) CALL (n) { DELETE n } IN TRANSACTIONS OF 20000 ROWS").consume()
        for statement in SCHEMA:
            session.run(statement).consume()
        session.run("CALL db.awaitIndexes(300)").consume()

    for filename, cypher, convert in STEPS:
        start = time.time()
        with open(BUILD / filename, encoding="utf-8", newline="") as f:
            rows = [convert(r) if convert else r for r in csv.DictReader(f)]
        for i in range(0, len(rows), BATCH):
            driver.execute_query(cypher, rows=rows[i:i + BATCH])
        print(f"  {filename:<24} {len(rows):>9,} rows  {time.time() - start:5.1f}s")

    print("Ranking drugs by interaction risk with Graph Data Science ...")
    driver.execute_query("CALL gds.graph.drop('interactions', false) YIELD graphName RETURN graphName")
    driver.execute_query("""
        CALL gds.graph.project('interactions', 'Drug',
             {INTERACTS_WITH: {orientation: 'UNDIRECTED', properties: 'weight'}})""")
    driver.execute_query("""
        CALL gds.degree.write('interactions', {relationshipWeightProperty: 'weight', writeProperty: 'risk_score'})""")
    driver.execute_query("CALL gds.graph.drop('interactions') YIELD graphName RETURN graphName")

    records, _, _ = driver.execute_query("""
        MATCH (n) WITH labels(n)[0] AS label, count(*) AS nodes RETURN label, nodes ORDER BY label""")
    print("Nodes:", ", ".join(f"{r['label']} {r['nodes']:,}" for r in records))
    records, _, _ = driver.execute_query("""
        MATCH ()-[r]->() WITH type(r) AS rel, count(*) AS n RETURN rel, n ORDER BY rel""")
    print("Relationships:", ", ".join(f"{r['rel']} {r['n']:,}" for r in records))
    driver.close()


if __name__ == "__main__":
    load()
