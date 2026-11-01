"""Score answers.json against expected answers, using the course's comparison rules.

    python evaluate.py --answers answers_public.json --expected expected_public.json

Expected file: a list of {query_id, expected_status, expected_answer: {type, value} or null}.
Rules: numbers within 0.1%; node lists as sets (in order if 'ordered': true); paths in order;
non-answered questions only need the right status. A null expected_answer is reported, not scored.
"""
import argparse
import json


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
    if t == "path" or exp.get("ordered"):
        return list(g or []) == list(v)
    if t == "node_list":
        return set(g or []) == set(v)
    return str(g).strip().lower() == str(v).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--expected", required=True)
    a = ap.parse_args()
    answers = {x["query_id"]: x for x in json.load(open(a.answers, encoding="utf-8"))}
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
