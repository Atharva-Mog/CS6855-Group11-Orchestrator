"""Score answers.json against expected answers, using the course's comparison rules.

    python evaluate.py --answers results/answers_public.json --expected results/CS6855_Public_Query_Set.csv
    python evaluate.py --answers results/answers_public.json --expected results/CS6855_Public_Query_Set.xlsx
    python evaluate.py --answers answers_public.json --expected expected_public.json

Expected CSV or Excel (the public query set; first sheet for Excel): columns 'Query ID', 'Graph', 'Expected status', 'Answer type',
'Expected answer' and 'Notes'. Answer types: number, boolean, node, path ('A → B → C'), node_list ('A, B, C').
A note containing 'order matters' makes a node_list ordered; one mentioning 'prerequisite' grades the list as
any valid topological order of data/<graph>/edges.csv. Other columns are ignored.
Expected JSON: a list of {query_id, expected_status, expected_answer: {type, value} or null}.
Rules: numbers within 0.1%; node lists as sets (in order if 'ordered': true); paths in order;
non-answered questions only need the right status. A null expected_answer is reported, not scored.
"""
import argparse
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
BLANK = {"", "—", "-", "nan", "none", "null"}


def parse_value(t, raw):
    if t == "number":
        return float(raw.replace(",", ""))
    if t == "boolean":
        return raw.lower() in ("true", "yes", "1")
    if t == "path":
        return [p.strip() for p in re.split(r"→|->", raw) if p.strip()]
    if t == "node_list":
        return [p.strip() for p in raw.split(",") if p.strip()]
    return raw


def read_table(path):
    """A .csv or .xlsx/.xls (first sheet) as a DataFrame of stripped strings with lower-cased column names.
    Rows without a query id (blank rows, footnotes) are dropped."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, sheet_name=0, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    df.columns = df.columns.str.strip().str.lower().str.replace("_", " ")
    df = df.fillna("").apply(lambda col: col.str.strip())
    return df[df["query id"] != ""].reset_index(drop=True)


def load_table_expected(path):
    df = read_table(path)
    for col in ("graph", "expected status", "answer type", "expected answer", "notes"):
        if col not in df:
            df[col] = ""
    df["expected status"] = df["expected status"].str.lower()
    df["answer type"] = df["answer type"].str.lower()
    df["notes"] = df["notes"].str.lower()
    expected = []
    for r in df.to_dict("records"):
        t, raw = r["answer type"], r["expected answer"]
        answer = None
        if t not in BLANK and raw.lower() not in BLANK:
            answer = {"type": t, "value": parse_value(t, raw)}
            if "order matters" in r["notes"]:
                answer["ordered"] = True
            if "prerequisite" in r["notes"]:
                answer["topological"] = r["graph"]
        expected.append({"query_id": r["query id"], "expected_status": r["expected status"], "expected_answer": answer})
    return expected


def is_topological_order(graph, order):
    """True if `order` lists every node of the graph exactly once with each edge's source before its target."""
    edges = pd.read_csv(ROOT / "data" / graph / "edges.csv", dtype=str)
    nodes = set(pd.read_csv(ROOT / "data" / graph / "nodes.csv", dtype=str)["id"])
    pos = {str(n): i for i, n in enumerate(order)}
    return (len(pos) == len(order) and set(pos) == nodes
            and all(pos[s] < pos[t] for s, t in zip(edges["source"], edges["target"])))


def same(exp, got):
    if got is None:
        return False
    t, v, g = exp["type"], exp["value"], got.get("value")
    if t == "number":
        try:
            return abs(float(g) - float(v)) <= max(1e-9, 1e-3 * abs(float(v)))
        except (TypeError, ValueError):
            return False
    if t == "boolean":
        return bool(g) == bool(v) and isinstance(g, bool)
    if t == "node":
        return str(g) == str(v) or (isinstance(g, list) and len(g) == 1 and g[0] == v)
    if exp.get("topological"):
        return isinstance(g, list) and is_topological_order(exp["topological"], g)
    if t == "path" or exp.get("ordered"):
        return [str(x) for x in (g or [])] == [str(x) for x in v]
    if t == "node_list":
        return {str(x) for x in (g or [])} == {str(x) for x in v}
    return str(g).strip().lower() == str(v).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--expected", required=True, help="the public query set as CSV/Excel, or an expected-answers JSON")
    a = ap.parse_args()
    answers = {x["query_id"]: x for x in json.load(open(a.answers, encoding="utf-8"))}
    if a.expected.lower().endswith((".csv", ".xlsx", ".xls")):
        expected = load_table_expected(a.expected)
    else:
        expected = json.load(open(a.expected, encoding="utf-8"))
    right = scored = 0
    for e in expected:
        q = e["query_id"]
        got = answers.get(q)
        if got is None:
            print(f"{q}: MISSING"); scored += 1; continue
        if e["expected_status"] != got["status"]:
            ok = False
        elif e["expected_status"] != "answered":
            ok = True
        elif e.get("expected_answer") is None:
            print(f"{q}: (no expected value) got {got.get('answer')}"); continue
        else:
            ok = same(e["expected_answer"], got.get("answer"))
        scored += 1; right += ok
        print(f"{q}: {'OK  ' if ok else 'FAIL'} status={got['status']} got={json.dumps(got.get('answer'))[:120]}"
              + ("" if ok else f"  expected={e['expected_status']} {json.dumps(e.get('expected_answer'))[:120]}"))
    print(f"\nScore: {right}/{scored} = {100 * right / max(scored, 1):.1f}%")


if __name__ == "__main__":
    main()
