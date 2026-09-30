# Graph-Based Medication Risk Analysis (Neo4j)

Checks a patient's whole medicine list (Indian brand or ingredient names) against each other,
against the patient's conditions and against hidden duplicate ingredients, and suggests safer,
cheaper alternatives. Every warning is explained as a path in the graph.

## First-time setup

Every command below starts from the project folder:

```bash
cd Medicine-Interaction-Analysis
```

Create the Python environment:

```bash
python3 -m venv .venv
```
```bash
.venv/bin/pip install -r requirements.txt
```

Copy the settings template, then set your own `NEO4J_PASSWORD` in `.env` (at least 8 characters):

```bash
cp .env.example .env
```

Create the Neo4j container with the Graph Data Science plugin, using the same password as in `.env`.
The database is stored in `neo4j-data/`, which is not committed:

```bash
docker run -d --name medicine-neo4j -p 7474:7474 -p 7687:7687 -v "$PWD/neo4j-data:/data" -e NEO4J_AUTH=neo4j/your-password -e NEO4J_PLUGINS='["graph-data-science"]' neo4j:5
```

Then build the database once (see "Rebuild the database" below).

## Run it

Start Docker Desktop, then Neo4j:

```bash
open -a Docker
```
```bash
docker start medicine-neo4j
```

Start the web page, then open http://localhost:5050

```bash
.venv/bin/python src/app.py
```

Stop the web page with Ctrl+C. Stop Neo4j with `docker stop medicine-neo4j`.

## Rebuild the database from the raw data

Only needed after changing the data or the scripts (about 1 minute):

```bash
.venv/bin/python src/prepare_data.py
```
```bash
.venv/bin/python src/load_graph.py
```

## Check nothing broke

Run after any change to the data, the queries or `src/curated.py` (about 5 seconds, read-only, needs Neo4j running).
It prints PASS/FAIL for 67 checks and ends with a count:

```bash
.venv/bin/python tests/run_regression.py
```

## Files

| Path | What it is |
|---|---|
| `data/` | Raw downloads: DDInter, DrugCentral, Hetionet, FDA enzyme table, India A-Z brands, Jan Aushadhi. The full DrugCentral database dump (`data/drugcentral/raw/`, 4.7 GB) is not committed; the pipeline only reads the exported TSVs |
| `build/` | Clean CSVs produced by `prepare_data.py` (generated, not committed) |
| `neo4j-data/` | The Neo4j database files (generated, not committed) |
| `src/prepare_data.py` | Cleans raw data into `build/*.csv`, matching drug names across sources via DrugCentral synonyms |
| `src/load_graph.py` | Loads the graph into Neo4j and ranks drugs with Graph Data Science (degree centrality) |
| `src/queries.py` | The six features as Cypher queries |
| `src/app.py`, `templates/index.html` | The Flask web page |
| `.env` | Your Neo4j password (never committed) |

## Graph model

- **Nodes:** Brand, Drug, Enzyme, Condition, SideEffect
- **Relationships:** `(Brand)-[:CONTAINS]->(Drug)`, `(Drug)-[:INTERACTS_WITH {severity}]-(Drug)`,
  `(Drug)-[:INHIBITS|INDUCES]->(Enzyme)`, `(Drug)-[:METABOLISED_BY]->(Enzyme)`,
  `(Drug)-[:TREATS]->(Condition)`, `(Drug)-[:CONTRAINDICATED_IN]->(Condition)`, `(Drug)-[:CAUSES]->(SideEffect)`

Educational prototype, not a certified clinical tool.
