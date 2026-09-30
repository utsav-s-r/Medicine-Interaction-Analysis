"""Turns the CDSCO prohibited fixed-dose-combination (FDC) PDFs into data/cdsco/banned_fdcs.csv.

Run once after downloading new lists (needs `pip install pypdf`, which the app itself does not need):
    python src/parse_cdsco.py
Source PDFs (https://cdsco.gov.in/opencms/opencms/en/Drugs/FDC/) go in data/cdsco/raw/:
    fdc16_2026.pdf       List of 16 FDCs banned dated 11.06.2026
    fdc156_2024.pdf      List of Prohibited FDC drugs, 156 FDCs dated 02.08.2024
    fdc14_2023.pdf       List of Prohibited FDC drugs, 14 FDCs dated 02.06.2023
    consolidated_2021.pdf  All drugs prohibited under Section 26A, with their status as on 22.11.2021

The consolidated list marks entries whose ban is not in force with footnotes; those rows are kept with a
status other than 'banned' so the reason stays visible. Only rows naming specific ingredients ("A + B") are
used: the 1980s class rules ("vitamins with analgesics") cannot be matched to ingredients reliably.
"""
import csv
import re

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/cdsco/raw"
OUT = ROOT / "data/cdsco/banned_fdcs.csv"
LISTS = [("fdc16_2026", "16 FDCs banned 11.06.2026"), ("fdc156_2024", "156 FDCs banned 02.08.2024"),
         ("fdc14_2023", "14 FDCs banned 02.06.2023"), ("consolidated_2021", "Consolidated list as on 22.11.2021")]
# footnotes of the consolidated list
STATUS = {"1": "stayed by the Madras High Court", "2": "ban revoked with conditions",
          "3": "under review (re-banned in the 2023 list if still banned)",
          "4": "under review (re-banned in the 2023 list if still banned)",
          "5": "ban quashed by the Delhi High Court, appeal pending", "6": "ban revoked with conditions"}
NOTIF = re.compile(r"(S\.\s?O\.?\s?(?:No\.)?\s?\d+\s?\(E\s?\)|G\.?S\.?R\.?\s?(?:NO\.?)?\s?\d+\s?\(E\s?\))", re.I)
DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def pdf_text(name):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(RAW / f"{name}.pdf").pages)


def main():
    rows = []
    for name, source in LISTS:
        text = pdf_text(name).split("1# Presently stayed")[0]          # the footnotes start here
        for m in re.finditer(r"(?ms)^\s*(\d{1,3})\.\s(.*?)(?=^\s*\d{1,3}\.\s|\Z)", text):
            body = m.group(2)
            flat = re.sub(r"\s+", " ", body)
            if "+" not in flat:
                continue
            n = NOTIF.search(flat)
            date = DATE.search(re.sub(r"\s", "", flat[n.start():])) if n else None
            marker = re.search(r"(\d)#", body)
            rows.append({"list": source, "item": m.group(1),
                         "combination": re.sub(r"\d#", "", flat[:n.start()] if n else flat).strip(" .;,"),
                         "notification": re.sub(r"\s+", " ", n.group(1)) if n else "",
                         "date": "-".join(reversed(date.groups())) if date else "",
                         "status": STATUS[marker.group(1)] if marker else "banned"})
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} combinations, {sum(r['status'] == 'banned' for r in rows)} banned -> {OUT}")


if __name__ == "__main__":
    main()
