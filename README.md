# Graph Analytics Orchestrator (CS6855 course project)

An LLM agent that answers natural-language questions about graphs by **planning, calling and chaining
graph-algorithm tools** from the class tool registry (built in Assignment 3). Every fact in an answer
comes from a tool result, and every question leaves a full trace.

```
python run_queries.py --queries <file> --out answers.json
```

> **Private course repository.** The rules say code is shared within the group only: keep this repo private.

---

## Quick start

Requires **Python 3.10+**.

```bash
git clone <this repo> && cd <repo>
python -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### API key (environment variable)

The orchestrator reads the key from the environment variable **`LLM_API_KEY`**. It talks to any
OpenAI-compatible endpoint, configured with two more variables:

| Variable | Meaning | Default |
| --- | --- | --- |
| `LLM_API_KEY` | API key (**required**) | none |
| `LLM_BASE_URL` | OpenAI-compatible endpoint | Gemini: `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `LLM_MODEL` | Model name | `gemini-3.8-flash` |
| `LLM_MIN_INTERVAL` | Optional: minimum seconds between LLM calls (for free-tier rate limits) | `0` |

Either export them, or copy `.env.example` to `.env` and fill it in (`.env` is git-ignored; never commit a key):

```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
```

### Run

The query file can be an Excel/CSV file (only the `Query ID` and `Question` columns are read) or JSON.

**Recommended: in batches**, with a pause between them so the API rate limit / quota isn't exhausted:

```bash
python run_batches.py --queries CS6855_Public_Query_Set.xlsx --out-dir . --batch-size 5 --sleep 60
```

Answers go to `<out-dir>/answers_public.json` and traces to `<out-dir>/traces/`. If it stops midway,
re-run the same command: questions already answered are skipped.

**Or directly, all at once:**

```bash
python run_queries.py --queries CS6855_Public_Query_Set.xlsx --out answers_public.json
```

- One trace per question goes to `traces/<query_id>.jsonl`.
- `--only q03,q07` runs just those questions (other answers in the output file are kept).
- LLM responses are cached in `.llm_cache/`, so re-running costs nothing; `--no-cache` forces fresh calls.

### Score against expected answers

```bash
python evaluate.py --answers answers_public.json --expected expected_public.json
```

Uses the course rules: numbers within 0.1%, node lists as sets (in order for rankings), paths in order,
and only the status for ambiguous or unsupported questions.

---

## Output format

One entry per question in `answers.json`:

```json
{
  "query_id": "q07",
  "status": "answered",
  "answer_text": "Member 34 (administrator) has the most friends, with 17.",
  "answer": {"type": "node", "value": "m34"},
  "tools_used": ["degree_centrality.c52598eb"],
  "trace_file": "traces/q07.jsonl"
}
```

`status` is `answered`, `clarification_needed`, `unsupported` or `error`.
`answer.type` is `number`, `boolean`, `node`, `node_list`, `path` or `text`; node answers use node **ids**.

---

## How it works

```
question ──► Planner (LLM + tool manifests) ──► tool calls ──► Executor ──► results ──┐
                ▲                                                                       │
                └──────────────── results, errors, result_graph_id ◄────────────────────┘
                                         │
                                         ▼
                              final_answer(status, type, value, text)  ──► answers.json + trace
```

1. **Tool discovery** (`orchestrator/registry.py`): at start-up, reads every `tool.json` in
   `registry/tools/<tool>/<implementation>/` and `infra_tools/`. Nothing is hard-coded.
2. **Planner** (`orchestrator/prompts.py`): a system prompt with the graph catalogue (type, size,
   attributes, example ids) and rules: look up names with `lookup_node`, take every fact from tools,
   chain through `result_graph_id`, default tool choices, keep calls to a minimum, and when to answer
   `unsupported` or `clarification_needed`.
3. **Executor** (`orchestrator/agent.py`): runs the tool calls the LLM asks for (in parallel when it
   asks for several), fixes simple argument types, turns crashes into error messages the LLM can
   recover from, caps output size and the number of calls, and records every step in the trace.
4. **Structured answer:** the LLM must finish by calling `final_answer` with status, type, value and
   text; the value is parsed into the right type for `answers.json`.
5. **LLM client** (`orchestrator/llm.py`): OpenAI-compatible, temperature 0, response cache,
   back-off and retry on rate limits, optional pacing.

The loop is plain Python (no agent framework).

### Tool curation

Each tool in the class registry had 3 to 8 student implementations. `curation/curate.py` tests every
candidate (in `candidates/`) against NetworkX on the provided graphs: correctness checks, error
handling (bad inputs must return an error, not crash), determinism and speed on `airports`. The best
one per tool is copied into `registry/tools/`. Results: `curation/report.md`.

```bash
pip install scipy                      # NetworkX needs it for some reference values (tests only)
python curation/curate.py              # test all candidates, write the report
python curation/curate.py --install    # also install the winners into registry/tools/
```

---

## Repository layout

Same structure as Assignment 3, plus the orchestrator:

| Path | What it is |
| --- | --- |
| `run_queries.py` | The one-command runner |
| `run_batches.py` | Runs `run_queries.py` in batches with pauses, to stay under rate limits |
| `evaluate.py` | Scores answers against expected answers |
| `orchestrator/` | Agent loop, planner prompts, tool discovery, LLM client |
| `registry/tools/` | The chosen implementation of each tool (one per tool) |
| `candidates/` | All class implementations, as released (input to curation) |
| `curation/` | Curation script and report |
| `dev/` | Practice questions with NetworkX-computed answers (`make_dev.py`) |
| `graphkit/`, `infra_tools/`, `data/` | Provided by the course; do not edit |
| `tests/` | Tests for the provided graphkit library |
