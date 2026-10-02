"""Small web page: enter a patient's medicines and conditions, see the ranked risks; and /graph, an interactive
explorer of the whole graph.

Run:  python src/app.py     then open http://localhost:5050
"""
from flask import Flask, abort, jsonify, render_template, request

import explore
import queries
from config import ROOT, get_driver

app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
driver = get_driver()


def _lines(text):
    return [t.strip() for t in (text or "").splitlines() if t.strip()]


# the most lines one check takes (the check page and the risk graph alike)
MAX_MEDICINES, MAX_CONDITIONS = 30, 20


def _age(text):
    """A whole number of years from 0 to 120, or None (blank or anything else)."""
    text = (text or "").strip()
    return int(text) if text.isdigit() and int(text) <= 120 else None


@app.get("/")
def index():
    medicines = request.args.get("medicines", "")
    conditions = request.args.get("conditions", "")
    new_medicine = request.args.get("new_medicine", "")
    age = _age(request.args.get("age"))
    result = None
    if _lines(medicines):
        meds, conds = _lines(medicines), _lines(conditions)
        result = queries.analyse(driver, meds[:MAX_MEDICINES], conds[:MAX_CONDITIONS], new_medicine[:200], age)
        result["cut"] = {"medicines": max(len(meds) - MAX_MEDICINES, 0), "conditions": max(len(conds) - MAX_CONDITIONS, 0)}
    return render_template("index.html", medicines=medicines, conditions=conditions, new_medicine=new_medicine,
                           age=age, result=result, top_risk=queries.top_risk_medicines(driver, 10))


# ---------------------------------------------------------------- graph explorer (/graph)

def _text(name, limit=200):
    return (request.args.get(name) or "").strip()[:limit]


@app.get("/graph")
def graph():
    """medicines/conditions/age: open the risk graph of that list; finding: open on that one warning's path;
    profile: open a drug's profile or an enzyme's hub ("Drug:DC:2847", "Enzyme:CYP3A");
    classes: open the drug-class map ("all", or a main group such as "B")."""
    return render_template("graph.html", medicines=_text("medicines", 2000), conditions=_text("conditions", 1000),
                           age=_text("age", 3), finding=_text("finding", 300), q=_text("q", 100),
                           profile=_text("profile", 120), classes=_text("classes", 3).upper())


@app.get("/api/graph/schema")
def graph_schema():
    return jsonify(explore.schema(driver))


@app.get("/api/graph/search")
def graph_search():
    return jsonify(explore.search(driver, _text("q", 100)))


@app.get("/api/graph/node")
def graph_node():
    found = explore.node(driver, _text("id"))
    return jsonify(found) if found else abort(404)


@app.get("/api/graph/expand")
def graph_expand():
    types = [t for t in _text("types", 500).split(",") if t] or None
    limit = _text("limit", 3)
    return jsonify(explore.expand(driver, _text("id"), types, int(limit) if limit.isdigit() else 25))


@app.get("/api/graph/risk")
def graph_risk():
    """The risk graph of a medicine list, checked exactly as on the check page (same medicines, conditions, age)."""
    medicines = _lines(_text("medicines", 2000))[:MAX_MEDICINES]
    if not medicines:
        return jsonify({"nodes": [], "edges": [], "findings": []})
    result = queries.analyse(driver, medicines, _lines(_text("conditions", 1000))[:MAX_CONDITIONS], None, _age(_text("age", 3)))
    return jsonify(explore.risk_graph(driver, result))


@app.get("/api/graph/profile")
def graph_profile():
    """A drug's profile or an enzyme's hub: label is Drug or Enzyme, key is the drug key or the enzyme name."""
    found = explore.profile(driver, _text("label", 20), _text("key", 100))
    return jsonify(found) if found else abort(404)


@app.get("/api/graph/classes")
def graph_classes():
    """The drug-class map: the 14 main groups, or with group=B the classes inside that group."""
    group = _text("group", 3).upper()
    if not group:
        return jsonify(explore.class_map(driver))
    found = explore.class_group(driver, group)
    return jsonify(found) if found else abort(404)


@app.get("/api/graph/class-pair")
def graph_class_pair():
    """The Major drug pairs between two drug classes (codes like B and M, or B01 and M01)."""
    found = explore.class_pair(driver, _text("a", 3).upper(), _text("b", 3).upper())
    return jsonify(found) if found else abort(404)


@app.get("/api/suggest")
def suggest():
    kind = "condition" if request.args.get("kind") == "condition" else "medicine"
    return jsonify(queries.suggest(driver, request.args.get("q", ""), kind))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
