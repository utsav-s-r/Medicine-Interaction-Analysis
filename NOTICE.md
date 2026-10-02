# Data notices

The files under `data/` come from other people and keep their own licences. They are copied unchanged, or cut down
by `src/extract_sources.py` and `src/parse_cdsco.py` (only rows and fields were removed). The generated `build/`
CSVs and the Neo4j database are not committed.

**Two sources (DDInter and SIDER) are licensed for non-commercial use only**, so this repository as a whole may only
be used non-commercially.

| Files | Source | Licence | Credit |
|---|---|---|---|
| `data/ddinter/` | DDInter, https://ddinter.scbdd.com | CC BY-NC-SA 4.0 | Xiong G et al. DDInter: an online drug-drug interaction database towards improving clinical decision-making and patient safety. Nucleic Acids Res 2022;50(D1):D1200-D1207 |
| `data/drugcentral/` | DrugCentral, https://drugcentral.org | CC BY-SA 4.0 | DrugCentral, Division of Translational Informatics, University of New Mexico |
| `data/hetionet/` | Hetionet v1.0, https://het.io | CC0 for Hetionet's own content; each node and edge keeps its source's licence (see the licence table at https://github.com/hetio/hetionet). This project uses only the drug-causes-side-effect edges, which come from SIDER 4.1: CC BY-NC-SA 4.0 | Himmelstein DS et al. Systematic integration of biomedical knowledge prioritizes drugs for repurposing. eLife 2017;6:e26726. SIDER: Kuhn M et al. Nucleic Acids Res 2016;44(D1):D1075-D1079 |
| `data/india/A_Z_medicines_dataset_of_India.csv` | A-Z Medicine Dataset of India, Kaggle (shudhanshusingh/az-medicine-dataset-of-india) | CC BY-SA 4.0 | Shudhanshu Singh |
| `data/india/medicine_details.csv.gz` | 250k Medicines Usage, Side Effects and Substitutes, Kaggle (shudhanshusingh/250k-medicines-usage-side-effects-and-substitutes), cut down | CC BY-SA 4.0; the extract is shared under the same licence | Shudhanshu Singh |
| `data/fda/fda_cyp_transporter_interacting_drugs.tsv` | US FDA, Drug Development and Drug Interactions: Table of Substrates, Inhibitors and Inducers | Public domain (US Government work) | US Food and Drug Administration |
| `data/fda/labels.jsonl.gz` | openFDA drug labels, https://open.fda.gov, cut down | CC0 1.0 (public domain) | openFDA, US Food and Drug Administration |
| `data/twosides/twosides_pairs.csv.gz` | TWOSIDES, Tatonetti lab, https://tatonettilab.org, cut down | No licence stated; the lab offers it free and open for academic use, asking that it be cited | Tatonetti NP, Ye PP, Daneshjou R, Altman RB. Data-driven prediction of drug effects and interactions. Sci Transl Med 2012;4(125):125ra31 |
| `data/jan_aushadhi/` | Jan Aushadhi product MRP list, Pharmaceuticals & Medical Devices Bureau of India (PMBI) | No licence stated; a public price list of the Government of India, included for reference | PMBI, Department of Pharmaceuticals, Government of India |
| `data/cdsco/banned_fdcs.csv` | Fixed-dose combinations banned under Section 26A of the Drugs & Cosmetics Act, CDSCO, cut down from the published notifications | Government of India notifications; no licence stated | Central Drugs Standard Control Organisation, Government of India |
| `data/beers/beers_2023.csv` | AGS Beers Criteria® 2023, written by hand in `src/make_beers.py` | The article is copyrighted and not committed; the criteria here are paraphrased, with table and page references | American Geriatrics Society 2023 Beers Criteria® Update Expert Panel. J Am Geriatr Soc 2023;71:2052-2081, doi:10.1111/jgs.18372 |

## Code from others

`static/cytoscape.min.js` is Cytoscape.js 3.30.2 (https://js.cytoscape.org), copyright (c) 2016-2024 The
Cytoscape Consortium, under the MIT licence. The file is unchanged and keeps its licence text at the top.

None of these sources guarantees that its data is complete or correct. This is an educational prototype, not a
certified clinical tool.
