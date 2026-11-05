"""Answer a file of graph questions.

    python run_queries.py --queries queries_public.json --out answers.json
    python run_queries.py --queries CS6855_Public_Query_Set.xlsx --out answers.json  (or .csv)

The API key is read from the environment variable LLM_API_KEY (or a .env file).
Traces go to traces/<query_id>.jsonl.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from graphkit.trace import Trace  # noqa: E402
from orchestrator.agent import Orchestrator  # noqa: E402
from orchestrator.llm import LLM  # noqa: E402


def load_table_queries(path: Path) -> list[dict]:
    """Reads the 'Query ID' and 'Question' columns of a CSV or Excel file (e.g. the course's public query set);
    other columns are ignored. For Excel, the first sheet is used. Rows missing either (blank rows, footnotes) are skipped."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, sheet_name=0, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    df.columns = df.columns.str.strip().str.lower().str.replace("_", " ")
    df = df[["query id", "question"]].fillna("").apply(lambda col: col.str.strip())
    df = df[(df["query id"] != "") & (df["question"] != "")]
    return df.rename(columns={"query id": "query_id"}).to_dict("records")


def load_queries(path: Path) -> list[dict]:
    """Accepts a CSV, an Excel file, a JSON list, a JSON object holding a list, or JSON lines.
    Each query needs an id and a question."""
    if path.suffix.lower() in (".csv", ".xlsx", ".xls"):
        return load_table_queries(path)
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(l) for l in text.splitlines() if l.strip()]
    else:
        data = [json.loads(l) for l in text.splitlines() if l.strip()]
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [data])
    queries = []
    for i, q in enumerate(data, 1):
        if isinstance(q, str):
            q = {"question": q}
        qid = q.get("query_id") or q.get("id") or f"q{i:02d}"
        question = q.get("question") or q.get("query") or q.get("text") or ""
        if q.get("graph_id") and q["graph_id"] not in question:
            question = f"{question} (graph: {q['graph_id']})"
        queries.append({"query_id": str(qid), "question": question})
    return queries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queries", required=True, help="file of questions (CSV/Excel with 'Query ID'/'Question' columns, JSON list or JSON lines)")
    ap.add_argument("--out", default="answers.json", help="where to write the answers")
    ap.add_argument("--traces", default="traces", help="folder for the trace files")
    ap.add_argument("--only", help="comma-separated query ids to run (for testing)")
    ap.add_argument("--no-cache", action="store_true", help="always call the LLM, ignoring cached responses")
    args = ap.parse_args()

    queries = load_queries(Path(args.queries))
    if args.only:
        keep = set(args.only.split(","))
        queries = [q for q in queries if q["query_id"] in keep]
    out_path = Path(args.out)
    previous = {}
    if args.only and out_path.exists():  # keep earlier answers when re-running a few questions
        previous = {a["query_id"]: a for a in json.loads(out_path.read_text(encoding="utf-8"))}

    orch = Orchestrator(LLM(use_cache=not args.no_cache))
    trace_dir = Path(args.traces)
    trace_dir.mkdir(parents=True, exist_ok=True)

    for q in queries:
        trace_file = trace_dir / f"{q['query_id']}.jsonl"
        trace = Trace(trace_file)
        try:
            res = orch.answer(q["question"], trace)
        except Exception as exc:
            trace.error(f"{type(exc).__name__}: {exc}")
            res = {"status": "error", "answer_text": f"{type(exc).__name__}: {exc}", "answer": None, "tools_used": []}
        entry = {"query_id": q["query_id"], **res, "trace_file": trace_file.as_posix()}
        previous[q["query_id"]] = entry
        print(f"{q['query_id']}: [{res['status']}] {res['answer_text'][:150]}", flush=True)
        order = [x["query_id"] for x in queries] + [k for k in previous if k not in {x["query_id"] for x in queries}]
        out_path.write_text(json.dumps([previous[k] for k in dict.fromkeys(order) if k in previous], indent=2), encoding="utf-8")

    print(f"Wrote {len(previous)} answers to {out_path}")


if __name__ == "__main__":
    main()
