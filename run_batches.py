"""Run run_queries.py in small batches with a pause between them, to stay under API rate limits.

    python run_batches.py --queries CS6855_Public_Query_Set.xlsx --out-dir results --batch-size 5 --sleep 60

--queries takes anything run_queries.py does: a CSV or Excel file ('Query ID' and 'Question' columns), or JSON.
Answers go to <out-dir>/answers_public.json and traces to <out-dir>/traces/.
Re-running skips queries that already have a non-error answer, so an interrupted run can be resumed.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from run_queries import load_queries  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queries", default=str(ROOT /"CS6855_Public_Query_Set.xlsx"),
                    help="file of questions (CSV/Excel with 'Query ID'/'Question' columns, JSON list or JSON lines)")
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--sleep", type=int, default=60, help="seconds to wait between batches")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "answers_public.json"
    trace_dir = out_dir / "traces"

    ids = [q["query_id"] for q in load_queries(Path(args.queries))]
    done = set()
    if out_path.exists():
        done = {a["query_id"] for a in json.loads(out_path.read_text(encoding="utf-8")) if a.get("status") != "error"}
    todo = [i for i in ids if i not in done]
    print(f"{len(ids)} queries, {len(done)} already answered, {len(todo)} to run")

    batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
    for n, batch in enumerate(batches, 1):
        print(f"\n=== Batch {n}/{len(batches)}: {', '.join(batch)} ===", flush=True)
        cmd = [sys.executable, str(ROOT / "run_queries.py"),
               "--queries", args.queries, "--out", str(out_path), "--traces", str(trace_dir),
               "--only", ",".join(batch)]
        if args.no_cache:
            cmd.append("--no-cache")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"Batch {n} exited with code {result.returncode}; continuing", flush=True)
        if n < len(batches):
            print(f"Sleeping {args.sleep}s before next batch...", flush=True)
            time.sleep(args.sleep)

    # --only puts the latest batch first; restore the original query order
    if out_path.exists():
        answers = {a["query_id"]: a for a in json.loads(out_path.read_text(encoding="utf-8"))}
        ordered = [answers[i] for i in ids if i in answers] + [a for k, a in answers.items() if k not in ids]
        out_path.write_text(json.dumps(ordered, indent=2), encoding="utf-8")
        print(f"\nDone. {len(ordered)} answers in {out_path}, traces in {trace_dir}")


if __name__ == "__main__":
    main()
