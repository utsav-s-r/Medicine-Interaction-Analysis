"""Small web page: enter a patient's medicines and conditions, see the ranked risks.

Run:  python src/app.py     then open http://localhost:5050
"""
from flask import Flask, jsonify, render_template, request

import queries
from config import ROOT, get_driver

app = Flask(__name__, template_folder=str(ROOT / "templates"))
driver = get_driver()


def _lines(text):
    return [t.strip() for t in (text or "").splitlines() if t.strip()]


@app.get("/")
def index():
    medicines = request.args.get("medicines", "")
    conditions = request.args.get("conditions", "")
    new_medicine = request.args.get("new_medicine", "")
    result = None
    if _lines(medicines):
        result = queries.analyse(driver, _lines(medicines), _lines(conditions), new_medicine)
    return render_template("index.html", medicines=medicines, conditions=conditions, new_medicine=new_medicine,
                           result=result, top_risk=queries.top_risk_medicines(driver, 10))


@app.get("/api/suggest")
def suggest():
    kind = "condition" if request.args.get("kind") == "condition" else "medicine"
    return jsonify(queries.suggest(driver, request.args.get("q", ""), kind))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
