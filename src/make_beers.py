"""Writes data/beers/beers_2023.csv, curated by hand from the AGS Beers Criteria 2023 (J Am Geriatr Soc
2023;71:2052-2081, doi:10.1111/jgs.18372). Reasons and advice are paraphrased, not copied. Each criterion names
its table and page so it can be checked against the article.

    python src/make_beers.py        (then rebuild: prepare_data.py, load_graph.py)

Drugs are named as in the graph (DrugCentral names) or by ATC class prefix; "@table7" means the Table 7 list.
points: 2 = avoid; 1 = only in some situations the app cannot see (why or how long it is taken); 0 = caution note.
"""
import csv
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data/beers/beers_2023.csv"

T7 = ("amitriptyline; amoxapine; clomipramine; desipramine; doxepin; imipramine; nortriptyline; paroxetine; "
      "prochlorperazine; promethazine; brompheniramine; chlorphenamine; cyproheptadine; dimenhydrinate; "
      "diphenhydramine; doxylamine; hydroxyzine; meclozine; triprolidine; darifenacin; fesoterodine; flavoxate; "
      "oxybutynin; solifenacin; tolterodine; trospium; benzatropine; trihexyphenidyl; chlorpromazine; clozapine; "
      "olanzapine; perphenazine; atropine; clidinium; dicycloverine; homatropine; hyoscyamine; scopolamine; "
      "cyclobenzaprine; orphenadrine")
BENZO = ("N05BA; N05CD", "clonazepam; clobazam")
OPIOID = ("N02A; N07BC", "codeine")
ANTIPSYCHOTIC = ("N05A", "lithium carbonate; lithium citrate")         # atc, excluded names
SNRI = "venlafaxine; desvenlafaxine; duloxetine; milnacipran; levomilnacipran"
# Beers counts gabapentinoids as antiepileptics, but ATC moved them to N02BF (painkillers) in 2023
GABAPENTINOIDS = "gabapentin; pregabalin"
# NSAIDs that are not COX-2 selective (M01A also holds glucosamine and diacerein, which are not NSAIDs)
NSAID = ("M01AA; M01AB; M01AC; M01AE; M01AG", "nabumetone; nimesulide")
COXIB = ("M01AH", "celecoxib; etoricoxib")
COLS = ["id", "table", "page", "kind", "title", "drugs", "atc", "exclude", "drugs_b", "atc_b", "condition",
        "min_count", "points", "advice", "reason", "evidence"]
rows = []


def row(id, table, page, kind, title, points, advice, reason, evidence, drugs="", atc="", exclude="",
        drugs_b="", atc_b="", condition="", min_count=""):
    rows.append(dict(id=id, table=table, page=page, kind=kind, title=title, drugs=drugs, atc=atc, exclude=exclude,
                     drugs_b=drugs_b, atc_b=atc_b, condition=condition, min_count=min_count, points=points,
                     advice=advice, reason=reason, evidence=evidence))


# ---- Table 2: medicines to avoid in older adults (pages 2057-2063). 2 points = avoid; 1 = only in some situations
row("T2-antihistamines", 2, 2057, "avoid", "First-generation (sedating) antihistamines", 2,
    "Avoid. A short course for a severe allergic reaction can still be appropriate.",
    "Strongly anticholinergic and cleared more slowly with age: confusion, dry mouth, constipation; long-term use "
    "is linked to falls, delirium and dementia.", "moderate / strong",
    drugs="brompheniramine; chlorphenamine; cyproheptadine; dimenhydrinate; diphenhydramine; doxylamine; "
          "hydroxyzine; meclozine; promethazine; triprolidine", atc="R06AA; R06AB; R06AD")
row("T2-nitrofurantoin", 2, 2057, "avoid", "Nitrofurantoin", 1,
    "Applies to long-term use to prevent urine infections, or when kidney function is low (CrCl under 30).",
    "Can harm the lungs, liver and nerves with long use; safer options exist.", "low / strong",
    drugs="nitrofurantoin")
row("T2-aspirin", 2, 2057, "avoid", "Aspirin for primary prevention", 1,
    "Applies if aspirin is taken only to prevent a first heart attack or stroke: do not start it, and ask about "
    "stopping it. Aspirin after a heart attack or stroke is usually still right.",
    "Bleeding risk rises sharply with age, and for prevention alone the harm can outweigh the benefit.",
    "high / strong", drugs="acetylsalicylic acid")
row("T2-warfarin", 2, 2057, "avoid", "Warfarin as a first choice", 1,
    "Applies when starting a blood thinner for atrial fibrillation or a clot: newer ones (DOACs such as apixaban) "
    "are preferred. Long-term users with a stable INR may reasonably continue.",
    "More major bleeding, especially in the brain, than the newer blood thinners, and no better protection.",
    "high / strong", drugs="warfarin")
row("T2-rivaroxaban", 2, 2058, "avoid", "Rivaroxaban for long-term use", 1,
    "Applies to long-term treatment of atrial fibrillation or clots: a safer blood thinner such as apixaban is "
    "usually preferred.", "Higher risk of major and stomach bleeding in older adults than other DOACs.",
    "moderate / strong", drugs="rivaroxaban")
row("T2-dipyridamole", 2, 2058, "avoid", "Dipyridamole (short-acting tablets)", 2,
    "Avoid. Does not apply to the long-acting capsule combined with aspirin.",
    "Can drop blood pressure on standing; better options exist.", "moderate / strong", drugs="dipyridamole")
row("T2-alpha1", 2, 2058, "avoid", "Doxazosin, prazosin, terazosin for blood pressure", 1,
    "Applies when used for blood pressure; use for prostate symptoms is a separate decision.",
    "High risk of dizziness and falls from blood pressure dropping on standing.", "moderate / strong",
    drugs="doxazosin; prazosin; terazosin")
row("T2-central-alpha", 2, 2058, "avoid", "Clonidine and similar for blood pressure", 1,
    "Applies when used as a first-choice blood pressure medicine.",
    "Drowsiness and confusion, slow heartbeat, and dizziness on standing.", "low / strong",
    drugs="clonidine; guanfacine; methyldopa")
row("T2-nifedipine", 2, 2058, "avoid", "Nifedipine, immediate-release", 1,
    "Applies to the quick-acting (immediate-release) form only; long-acting tablets are not included.",
    "Blood pressure can fall too fast and strain the heart.", "high / strong", drugs="nifedipine")
row("T2-amiodarone", 2, 2058, "avoid", "Amiodarone as a first choice for atrial fibrillation", 1,
    "Applies when it is the first medicine chosen for atrial fibrillation, unless there is heart failure or a "
    "thickened heart muscle.", "More side effects than other rhythm medicines.", "high / strong",
    drugs="amiodarone")
row("T2-dronedarone", 2, 2058, "avoid", "Dronedarone", 1,
    "Applies with permanent atrial fibrillation or severe or recently worsened heart failure.",
    "Worse outcomes in these patients.", "high / strong", drugs="dronedarone")
row("T2-digoxin", 2, 2059, "avoid", "Digoxin as a first choice", 1,
    "Applies when it is the first medicine chosen for atrial fibrillation or heart failure; if used, keep the "
    "dose at or under 0.125 mg a day.",
    "Safer options exist, and it builds up when the kidneys slow with age.", "low-moderate / strong",
    drugs="digoxin")
row("T2-anticholinergic-antidepressants", 2, 2059, "avoid", "Strongly anticholinergic antidepressants", 2,
    "Avoid. (Very low-dose doxepin, 6 mg a day or less, is an exception.)",
    "Strongly anticholinergic, sedating, and cause dizziness on standing.", "high / strong",
    drugs="amitriptyline; amoxapine; clomipramine; desipramine; doxepin; imipramine; nortriptyline; paroxetine")
row("T2-antiparkinson-anticholinergic", 2, 2059, "avoid", "Benzatropine, trihexyphenidyl", 2,
    "Avoid, including for side effects of antipsychotics.",
    "Strongly anticholinergic; better treatments exist for Parkinson disease.", "moderate / strong",
    drugs="benzatropine; trihexyphenidyl")
row("T2-antipsychotics", 2, 2059, "avoid", "Antipsychotics", 1,
    "Applies unless used for schizophrenia, bipolar disorder or another approved reason. Avoid for behaviour "
    "problems in dementia or delirium unless other approaches have failed.",
    "Higher risk of stroke, faster memory decline and death in people with dementia.", "moderate / strong",
    atc=ANTIPSYCHOTIC[0], exclude=ANTIPSYCHOTIC[1])
row("T2-barbiturates", 2, 2060, "avoid", "Barbiturates", 2, "Avoid.",
    "Easy to become dependent on, stop helping sleep, and risk of overdose even at low doses.", "high / strong",
    drugs="butalbital; phenobarbital; primidone", atc="N03AA; N05CA")
row("T2-benzodiazepines", 2, 2060, "avoid", "Benzodiazepines", 2,
    "Avoid. May still be right for seizures, alcohol or benzodiazepine withdrawal, severe anxiety or before a "
    "procedure.",
    "Older adults are more sensitive: confusion, falls, fractures and road accidents; dependence and misuse; "
    "dangerous with opioids.", "moderate / strong", drugs=BENZO[1], atc=BENZO[0])
row("T2-z-drugs", 2, 2060, "avoid", "Z-drug sleeping tablets", 2, "Avoid.",
    "Same harms as benzodiazepines (confusion, falls, fractures, hospital visits) for little extra sleep.",
    "moderate / strong", drugs="eszopiclone; zaleplon; zolpidem", atc="N05CF")
row("T2-meprobamate", 2, 2060, "avoid", "Meprobamate", 2, "Avoid.", "Very sedating and easy to become dependent on.",
    "moderate / strong", drugs="meprobamate")
row("T2-ergoloid", 2, 2060, "avoid", "Ergoloid mesylates", 2, "Avoid.", "Does not work.", "high / strong",
    drugs="ergoloid mesylates")
row("T2-androgens", 2, 2061, "avoid", "Testosterone", 1,
    "Applies unless there is confirmed low testosterone with symptoms.",
    "Possible heart problems, and risk in men with prostate cancer.", "moderate / weak",
    drugs="methyltestosterone; testosterone")
row("T2-estrogens", 2, 2061, "avoid", "Estrogen tablets or patches (HRT)", 1,
    "Applies to tablets and patches: do not start them at this age, and ask about stopping. Low-dose vaginal "
    "creams or tablets are fine.",
    "Started at 60 or older, the risks (breast and womb cancer, heart disease, stroke, clots, dementia) outweigh "
    "the benefits.", "high / strong", atc="G03C")
row("T2-sulfonylureas", 2, 2061, "avoid", "Sulfonylureas for diabetes", 1,
    "Applies when used as a first or second diabetes medicine while safer ones are available. If one is needed, "
    "a short-acting one (glipizide) is preferred over glimepiride or glibenclamide.",
    "More low sugar episodes, heart problems and deaths than other diabetes medicines.",
    "high-moderate / strong", drugs="gliclazide; glimepiride; glipizide; glibenclamide", atc="A10BB")
row("T2-thyroid", 2, 2061, "avoid", "Desiccated thyroid", 2, "Avoid; use levothyroxine instead.",
    "Concerns about effects on the heart.", "low / strong", drugs="thyroid, porcine")
row("T2-megestrol", 2, 2062, "avoid", "Megestrol", 2, "Avoid.",
    "Adds little weight and raises the risk of clots and possibly death.", "moderate / strong",
    drugs="megestrol acetate; megestrol")
row("T2-growth-hormone", 2, 2062, "avoid", "Growth hormone", 1,
    "Applies unless there is a proven growth hormone deficiency.",
    "Small benefit with swelling, joint pain and raised blood sugar.", "high / strong", drugs="somatropin")
row("T2-ppi", 2, 2062, "avoid", "Stomach acid tablets (PPIs) for more than 8 weeks", 1,
    "Applies when taken every day for more than 8 weeks without a clear reason (such as long-term steroids or "
    "painkillers, or severe reflux damage).",
    "Long-term use is linked to gut infections (C. difficile), pneumonia, bone loss and fractures.",
    "high-moderate / strong", atc="A02BC")
row("T2-metoclopramide", 2, 2062, "avoid", "Metoclopramide", 2,
    "Avoid, except for gastroparesis for no more than 12 weeks.",
    "Can cause movement disorders, sometimes permanent (tardive dyskinesia).", "moderate / strong",
    drugs="metoclopramide")
row("T2-antispasmodics", 2, 2062, "avoid", "Strongly anticholinergic stomach antispasmodics", 2,
    "Avoid (atropine eye drops are not included).", "Strongly anticholinergic, and it is unclear that they help.",
    "moderate / strong", drugs="atropine; clidinium; dicycloverine; hyoscyamine; scopolamine")
row("T2-mineral-oil", 2, 2062, "avoid", "Liquid paraffin (mineral oil) by mouth", 2, "Avoid; use another laxative.",
    "Can be breathed into the lungs by mistake.", "moderate / strong",
    drugs="liquid paraffin; mineral oil; light mineral oil; white mineral oil")
row("T2-desmopressin", 2, 2062, "avoid", "Desmopressin for night-time urination", 1,
    "Applies when used for passing urine at night.", "High risk of low sodium in the blood.", "moderate / strong",
    drugs="desmopressin")
row("T2-nsaids", 2, 2062, "avoid", "Painkillers of the NSAID type, long-term", 1,
    "Applies to regular long-term use, or any regular use with steroids or blood thinners, unless nothing else "
    "works and a stomach-protecting tablet is taken too.",
    "Stomach ulcers and bleeding (more likely over 75), raised blood pressure and kidney damage.",
    "moderate / strong", drugs=NSAID[1], atc=NSAID[0], exclude="indometacin; ketorolac")
row("T2-indomethacin-ketorolac", 2, 2063, "avoid", "Indomethacin and ketorolac", 2, "Avoid.",
    "The highest risk of stomach bleeding and kidney injury of all NSAIDs; indomethacin also affects the brain.",
    "moderate / strong", drugs="indometacin; ketorolac")
row("T2-pethidine", 2, 2063, "avoid", "Pethidine (meperidine)", 2, "Avoid; safer painkillers exist.",
    "Works poorly by mouth and can cause confusion and other nerve toxicity.", "moderate / strong",
    drugs="pethidine")
row("T2-muscle-relaxants", 2, 2063, "avoid", "Muscle relaxants", 2,
    "Avoid. (Baclofen and tizanidine for spasticity are not included.)",
    "Poorly tolerated: anticholinergic effects, drowsiness and fractures, with doubtful benefit at safe doses.",
    "moderate / strong",
    drugs="carisoprodol; chlorzoxazone; cyclobenzaprine; metaxalone; methocarbamol; orphenadrine")

# ---- Table 3: avoid with these conditions (pages 2065-2067), only when the patient has the condition
row("T3-heart-failure", 3, 2065, "condition", "Worsens heart failure", 2,
    "Avoid cilostazol; avoid diltiazem and verapamil if the heart pumps weakly; avoid NSAIDs, pioglitazone and "
    "dronedarone if heart failure causes symptoms.",
    "Can cause fluid build-up and make heart failure worse, or raise the risk of death.", "low-high / strong",
    drugs="cilostazol; diltiazem; verapamil; dronedarone; pioglitazone; rosiglitazone; " + NSAID[1],
    atc=NSAID[0] + "; " + COXIB[0],
    condition="Heart failure")
row("T3-syncope", 3, 2065, "condition", "Worsens fainting", 2, "Avoid.",
    "Lower blood pressure on standing or slow the heartbeat, which can cause more fainting.",
    "high / weak-strong",
    drugs="chlorpromazine; olanzapine; donepezil; galantamine; rivastigmine; doxazosin; prazosin; terazosin; "
          "amitriptyline; clomipramine; doxepin; imipramine", condition="Fainting (syncope)")
row("T3-delirium", 3, 2066, "condition", "Worsens delirium (sudden confusion)", 2,
    "Avoid in anyone with, or at high risk of, delirium. Steroids, if needed, at the lowest dose for the "
    "shortest time.", "Can bring on or worsen sudden confusion.", "low-moderate / strong",
    drugs="@table7; " + BENZO[1] + "; cimetidine; famotidine; nizatidine; eszopiclone; zaleplon; zolpidem; codeine",
    atc="N05A; N05BA; N05CD; H02AB; A02BA; N05CF; N02A", exclude=ANTIPSYCHOTIC[1], condition="Delirium")
row("T3-dementia", 3, 2066, "condition", "Worsens dementia", 2,
    "Avoid. Antipsychotics only when other approaches for behaviour problems have failed.",
    "Harm memory and thinking; antipsychotics also raise the risk of stroke and death in dementia.",
    "moderate / strong", drugs="@table7; " + BENZO[1] + "; eszopiclone; zaleplon; zolpidem",
    atc="N05A; N05BA; N05CD; N05CF", exclude=ANTIPSYCHOTIC[1], condition="Dementia")
row("T3-falls", 3, 2066, "condition", "Raises the risk of another fall", 2,
    "Avoid unless there is no safer option; antiepileptics only for seizures or mood disorders, opioids only for "
    "severe short-term pain. If one is needed, cut down the others on this list.",
    "Unsteadiness, slowed reactions and fainting lead to more falls and fractures.", "moderate-high / strong",
    drugs="@table7; " + BENZO[1] + "; " + SNRI + "; " + GABAPENTINOIDS + "; eszopiclone; zaleplon; zolpidem; codeine",
    atc="N06AB; N06AA; N03; N05A; N05BA; N05CD; N05CF; N02A", exclude=ANTIPSYCHOTIC[1],
    condition="History of falls or fractures")
row("T3-parkinson", 3, 2067, "condition", "Worsens Parkinson disease", 2,
    "Avoid. (Clozapine, pimavanserin and quetiapine are less likely to cause problems.)",
    "Block dopamine and can make Parkinson symptoms worse.", "moderate / strong",
    drugs="metoclopramide; prochlorperazine; promethazine", atc="N05A",
    exclude="clozapine; pimavanserin; quetiapine; lithium carbonate; lithium citrate",
    condition="Parkinson's disease")
row("T3-ulcer", 3, 2067, "condition", "Worsens stomach ulcers", 2,
    "Avoid unless nothing else works and a stomach-protecting tablet is taken too.",
    "Can worsen ulcers or cause new ones.", "moderate / strong", drugs="acetylsalicylic acid; " + NSAID[1],
    atc=NSAID[0], condition="Peptic ulcer")
row("T3-incontinence", 3, 2067, "condition", "Worsens urine leakage in women", 2,
    "Avoid in women (vaginal estrogen is not included).",
    "Alpha-blockers make leakage worse; estrogen tablets do not help it.", "moderate-high / strong",
    drugs="doxazosin; prazosin; terazosin", atc="G03C", condition="Urinary incontinence")
row("T3-prostate", 3, 2067, "condition", "Worsens prostate or bladder emptying problems in men", 2,
    "Avoid in men (bladder medicines for leakage are a separate decision).",
    "Weaken the urine flow and can stop urine altogether.", "moderate / strong",
    drugs="@table7", exclude="darifenacin; fesoterodine; flavoxate; oxybutynin; solifenacin; tolterodine; trospium",
    condition="Enlarged prostate")

# ---- Table 4: use with caution (page 2068): shown as notes, not scored
row("T4-dabigatran", 4, 2068, "caution", "Dabigatran", 0,
    "Other blood thinners (such as apixaban) are usually preferred for long-term use.",
    "More stomach and major bleeding than some alternatives.", "moderate / strong", drugs="dabigatran etexilate")
row("T4-prasugrel", 4, 2068, "caution", "Prasugrel, ticagrelor", 0,
    "Use with care, especially from age 75; for prasugrel a lower dose (5 mg) is advised at 75 and over.",
    "More major bleeding than clopidogrel in older adults.", "moderate / strong", drugs="prasugrel; ticagrelor")
row("T4-sodium", 4, 2068, "caution", "Can lower sodium in the blood", 0,
    "Check the blood sodium level when starting or changing the dose.",
    "Can cause low sodium (SIADH), which brings confusion, falls and fits.", "moderate / strong",
    drugs="mirtazapine; carbamazepine; oxcarbazepine; tramadol; " + SNRI, atc="N06AB; N06AA; N05A; C03",
    exclude=ANTIPSYCHOTIC[1])
row("T4-sglt2", 4, 2068, "caution", "SGLT2 diabetes medicines (gliflozins)", 0,
    "Watch for urine and genital infections, especially in the first month, and for ketoacidosis.",
    "Older adults are more prone to these infections and to ketoacidosis with normal sugar.", "moderate / weak",
    atc="A10BK")

# ---- Table 5: drug combinations to avoid (pages 2070-2071). Only when both are in the list.
RAS = "C09"
row("T5-ras", 5, 2070, "pair", "Two blood pressure medicines that raise potassium", 1,
    "Avoid using two of these together routinely, especially with kidney disease (stage 3a or worse).",
    "Potassium in the blood can rise too high.", "moderate / strong",
    drugs="amiloride; triamterene", atc=RAS, drugs_b="amiloride; triamterene", atc_b=RAS)
row("T5-opioid-benzo", 5, 2070, "pair", "Opioid with a benzodiazepine", 2, "Avoid.",
    "Higher risk of overdose and dangerous side effects.", "moderate / strong",
    drugs=OPIOID[1], atc=OPIOID[0], drugs_b=BENZO[1], atc_b=BENZO[0])
row("T5-opioid-gabapentinoid", 5, 2070, "pair", "Opioid with gabapentin or pregabalin", 2,
    "Avoid, except while switching from one to the other or using them to lower the opioid dose.",
    "Severe drowsiness, slowed breathing and death.", "moderate / strong",
    drugs=OPIOID[1], atc=OPIOID[0], drugs_b=GABAPENTINOIDS)
row("T5-lithium-ras", 5, 2070, "pair", "Lithium with an ACE inhibitor or ARB", 2,
    "Avoid; if used, check lithium blood levels.", "Lithium can build up to toxic levels.", "moderate / strong",
    drugs="lithium carbonate; lithium citrate", atc_b="C09A; C09B; C09C; C09D")
row("T5-lithium-loop", 5, 2070, "pair", "Lithium with a loop diuretic", 2,
    "Avoid; if used, check lithium blood levels.", "Lithium can build up to toxic levels.", "moderate / strong",
    drugs="lithium carbonate; lithium citrate", atc_b="C03CA")
row("T5-alpha1-loop", 5, 2070, "pair", "Doxazosin/prazosin/terazosin with a loop diuretic", 1,
    "Applies to older women, unless both are really needed.", "More urine leakage.", "moderate / strong",
    drugs="doxazosin; prazosin; terazosin", atc_b="C03CA")
row("T5-phenytoin-cotrimoxazole", 5, 2070, "pair", "Phenytoin with co-trimoxazole", 2, "Avoid.",
    "Phenytoin can build up to toxic levels.", "moderate / strong",
    drugs="phenytoin", drugs_b="trimethoprim; sulfamethoxazole")
row("T5-theophylline", 5, 2071, "pair", "Theophylline with cimetidine or ciprofloxacin", 2, "Avoid.",
    "Theophylline can build up to toxic levels.", "moderate / strong",
    drugs="theophylline", drugs_b="cimetidine; ciprofloxacin")
row("T5-warfarin", 5, 2071, "pair", "Warfarin with a medicine that raises bleeding risk", 2,
    "Avoid when possible; if both are needed, check the INR closely.", "More bleeding.", "moderate / strong",
    drugs="warfarin", drugs_b="amiodarone; ciprofloxacin; trimethoprim; sulfamethoxazole",
    atc_b="J01FA; N06AB", exclude="azithromycin")
row("T5-anticholinergic", 5, 2070, "count", "Two or more anticholinergic medicines", 2,
    "Avoid; keep the number of anticholinergic medicines as low as possible.",
    "Together they raise the risk of memory decline, delirium, falls and fractures.", "moderate / strong",
    drugs="@table7", min_count=2)
row("T5-cns", 5, 2070, "count", "Three or more medicines acting on the brain", 2,
    "Avoid taking three or more together; keep the number as low as possible.",
    "More falls and fractures.", "high / strong",
    drugs=BENZO[1] + "; " + SNRI + "; " + GABAPENTINOIDS + "; eszopiclone; zaleplon; zolpidem; codeine",
    atc="N03; N06AA; N06AB; N05A; N05BA; N05CD; N05CF; N02A; N07BC; M03B", exclude=ANTIPSYCHOTIC[1], min_count=3)

# ---- Table 7: strongly anticholinergic medicines (page 2074), referred to as @table7
row("T7", 7, 2074, "list", "Strongly anticholinergic medicines", 0, "", "", "", drugs=T7)

with open(OUT, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    w.writerows(rows)
print(len(rows), "criteria")
