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

The **Graph explorer** is at http://localhost:5050/graph. After a check, "see them as a graph" opens the
**Risks** view: the medicine list with only the connections that produced its warnings (nothing else), and
"show path" next to a warning opens just that warning's path. **Profile** shows a drug's meaningful connections in
labelled boxes (its type of medicine, liver enzymes, serious interactions, what it treats, conditions it is unsafe
with, Beers rules, its label, how many brands), or a liver enzyme's hub (the drugs that block it, speed it up, or are cleared by it, and how
many of those pairs have no direct interaction record). Searching a drug or enzyme opens its profile, and every
ingredient on the check page links to one. **Drug classes** shows which types of medicine clash most: the 14 main
groups of the WHO ATC classification, linked by how many drug pairs between them have a Major interaction (or what
share of all possible pairs). Open a group to see its classes and the classes they clash with, and click a link for
the classes and example drug pairs behind it (medicines sold in India first), e.g. Blood → antithrombotics ↔
anti-inflammatories → warfarin + ibuprofen. **Explore** browses any node and its neighbours; **Schema** shows the
node and relationship types. Links straight to a view: `/graph?profile=Drug:DC:2847`, `/graph?profile=Enzyme:CYP3A`,
`/graph?classes=all`, `/graph?classes=B`. The page loads its drawing library (Cytoscape.js) from the jsDelivr CDN,
so it needs an internet connection.

Stop the web page with Ctrl+C. Stop Neo4j with `docker stop medicine-neo4j`.

## Rebuild the database from the raw data

Only needed after changing the data or the scripts (about 1 minute):

```bash
.venv/bin/python src/prepare_data.py
```
```bash
.venv/bin/python src/load_graph.py
```

### Refreshing the extra sources (rarely)

The committed files `data/fda/labels.jsonl.gz`, `data/twosides/twosides_pairs.csv.gz`,
`data/india/medicine_details.csv.gz` and `data/cdsco/banned_fdcs.csv` are small cuts of large downloads, so the rebuild above never needs the downloads.
To refresh them, download into the (not committed) `raw/` folders and cut them down again:

- openFDA drug labels: the 14 `drug-label-*.json.zip` files listed at https://api.fda.gov/download.json
  (about 1.8 GB) into `data/fda/raw/`
- TWOSIDES: https://tatonettilab-resources.s3.us-west-1.amazonaws.com/nsides/TWOSIDES.csv.gz (about 740 MB)
  into `data/twosides/raw/`
- 250k Indian medicines: `medicine_dataset.csv` from
  https://www.kaggle.com/datasets/shudhanshusingh/250k-medicines-usage-side-effects-and-substitutes (about 90 MB,
  free Kaggle login) into `data/india/raw/`
- CDSCO banned combination lists: the PDFs named at the top of `src/parse_cdsco.py`, from
  https://cdsco.gov.in/opencms/opencms/en/Drugs/FDC/, into `data/cdsco/raw/`

```bash
.venv/bin/python src/extract_sources.py
```
```bash
.venv/bin/pip install pypdf && .venv/bin/python src/parse_cdsco.py
```

The AGS Beers Criteria (medicines to avoid over 65) are curated by hand in `src/make_beers.py`, which writes
`data/beers/beers_2023.csv`; each criterion names its table and page. The article itself (J Am Geriatr Soc
2023;71:2052-2081, doi:10.1111/jgs.18372) is copyrighted and is not committed; keep your copy in `data/beers/raw/`
to check the criteria against. After editing:

```bash
.venv/bin/python src/make_beers.py
```

Then rebuild the database as above. Check the banned list by hand after `parse_cdsco.py`: a wrong "banned" label
is worse than a missing one.

## Check nothing broke

Run after any change to the data, the queries or `src/curated.py` (about 5 seconds, read-only, needs Neo4j running).
It prints PASS/FAIL for 115 checks and ends with a count:

```bash
.venv/bin/python tests/run_regression.py
```

## Files

| Path | What it is |
|---|---|
| `data/` | Raw downloads: DDInter, DrugCentral, Hetionet, FDA enzyme table, India A-Z brands, Jan Aushadhi, plus cut-down extracts of the US drug labels (openFDA), TWOSIDES and the CDSCO banned lists. The big originals (`data/*/raw/`) are not committed |
| `build/` | Clean CSVs produced by `prepare_data.py` (generated, not committed) |
| `neo4j-data/` | The Neo4j database files (generated, not committed) |
| `src/prepare_data.py` | Cleans raw data into `build/*.csv`, matching drug names across sources via DrugCentral synonyms |
| `src/extract_sources.py`, `src/parse_cdsco.py` | Cut the big downloads (openFDA labels, TWOSIDES, CDSCO PDFs) down to the committed extracts |
| `src/make_beers.py` | The AGS Beers Criteria 2023, curated by hand (paraphrased), written to `data/beers/beers_2023.csv` |
| `src/curated.py` | Small hand-made lists: everyday condition words, prescribing cascades, effect groups ("what can happen"), how labels name drug groups, serious reported events |
| `src/load_graph.py` | Loads the graph into Neo4j and ranks drugs with Graph Data Science (degree centrality) |
| `src/queries.py` | The six features as Cypher queries |
| `src/app.py`, `templates/index.html` | The Flask web page (medicine check) |
| `src/explore.py`, `templates/graph.html` | The graph explorer (/graph): the risk graph of a checked list, one warning's path, drug profiles, enzyme hubs, the drug-class map, browsing, schema |
| `.env` | Your Neo4j password (never committed) |

## Graph model

- **Nodes:** Brand, Drug, Enzyme, Condition, SideEffect, Label (official US drug label), Ban (CDSCO notification),
  Guideline (one AGS Beers Criteria 2023 criterion), DrugClass (the top two ATC levels: 14 main groups such as
  Blood, and classes such as antithrombotic agents)
- **Relationships:** `(Brand)-[:CONTAINS]->(Drug)`, `(Drug)-[:INTERACTS_WITH {severity}]-(Drug)`,
  `(Drug)-[:INHIBITS|INDUCES]->(Enzyme)`, `(Drug)-[:METABOLISED_BY]->(Enzyme)`,
  `(Drug)-[:TREATS]->(Condition)`, `(Drug)-[:CONTRAINDICATED_IN]->(Condition)`, `(Drug)-[:CAUSES]->(SideEffect)`,
  `(Drug)-[:HAS_LABEL]->(Label)`, `(Drug)-[:LABEL_MENTIONS {sentences}]->(Drug)` (A's label names B),
  `(Drug)-[:REPORTED_TOGETHER {effects, reports}]-(Drug)` (TWOSIDES), `(Brand)-[:BANNED_UNDER]->(Ban)`,
  `(Brand)-[:SUBSTITUTE {same_strength}]->(Brand)` (same ingredients and strengths, another maker),
  `(Drug)-[:FLAGGED_BY {side}]->(Guideline)` (Beers; `side` a/b for drug combinations),
  `(Drug)-[:BELONGS_TO]->(DrugClass)-[:PART_OF]->(DrugClass)` (class, then its main group)
- Indian brands also carry `uses`, `side_effects`, `habit_forming` and drug class properties

## Data sources

DDInter (interaction severity), DrugCentral (names, drug classes, what drugs treat and must not be used in),
Hetionet/SIDER (side effects), US FDA enzyme table, A-Z Medicine Dataset of India and 250k Indian medicines
(Kaggle, shudhanshusingh, CC BY-SA 4.0: brand uses, side effects, habit forming, substitutes; the extract in this
repository is shared under the same licence), Jan Aushadhi price list,
openFDA drug labels (public domain: what the label says about each drug and pair), TWOSIDES (Tatonetti lab:
side effects reported for drug pairs in the US FDA adverse event reports), CDSCO lists of fixed-dose combinations
banned under Section 26A of the Drugs & Cosmetics Act (2018 onwards, as in force), and the American Geriatrics
Society 2023 Beers Criteria® (Tables 2-5 and 7, paraphrased; applied when the patient's age is 65 or over).

Educational prototype, not a certified clinical tool.
