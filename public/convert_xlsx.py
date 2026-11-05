"""Turn the instructors' spreadsheet into queries_public.json and expected_public.json."""
import json, re, sys
from pathlib import Path
import openpyxl

HERE = Path(__file__).resolve().parent
ws = openpyxl.load_workbook(HERE / "CS6855_Public_Query_Set.xlsx", data_only=True)["Queries"]
rows = [r for r in ws.iter_rows(values_only=True)]
head = rows[0]
queries, expected = [], []
for r in rows[1:]:
    d = dict(zip(head, r))
    qid = d.get("Query ID")
    if not qid or not str(qid).startswith("q"):
        continue
    graph = d["Graph"] if d["Graph"] and d["Graph"] != "(none)" else None
    q = {"query_id": qid, "question": d["Question"]}
    if graph:
        q["graph_id"] = graph
    queries.append(q)
    status, atype, raw = d["Expected status"], d["Answer type"], d["Expected answer"]
    ans = None
    if status == "answered":
        if atype == "number":
            val = float(str(raw).replace(",", ""))
            ans = {"type": "number", "value": int(val) if val.is_integer() else val}
        elif atype == "boolean":
            ans = {"type": "boolean", "value": str(raw).strip().lower() == "true"}
        elif atype == "node":
            ans = {"type": "node", "value": str(raw).strip()}
        elif atype == "path":
            ans = {"type": "path", "value": [x.strip() for x in re.split(r"→|->|,", str(raw)) if x.strip()]}
        elif atype == "node_list":
            ans = {"type": "node_list", "value": [x.strip() for x in str(raw).split(",") if x.strip()]}
            notes = f"{d.get('Notes') or ''} {d.get('Working (NetworkX)') or ''}".lower()
            if "order matters" in notes:
                ans["ordered"] = True
            if "grade by" in notes and "valid" in notes:
                ans["check"] = "topological_order"
                ans["graph_id"] = graph
    expected.append({"query_id": qid, "expected_status": status, "expected_answer": ans})
(HERE.parent / "queries_public.json").write_text(json.dumps(queries, indent=1), encoding="utf-8")
(HERE / "expected_public.json").write_text(json.dumps(expected, indent=1), encoding="utf-8")
print(len(queries), "queries")
