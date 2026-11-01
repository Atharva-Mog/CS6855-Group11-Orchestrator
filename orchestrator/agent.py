"""The agent loop: plan -> call tools (chaining result_graph_id) -> final_answer, with a full trace."""
from __future__ import annotations

import json

from graphkit import GraphStore
from graphkit.trace import Trace

from .prompts import FINAL_ANSWER_TOOL, SYSTEM, graph_catalogue
from .registry import ToolBox

MAX_STEPS = 8          # LLM calls per question
MAX_TOOL_CALLS = 8     # graph-tool calls per question
MAX_TOOL_OUTPUT = 6000 # characters of tool output sent back to the LLM
ANSWER_TYPES = {"number", "boolean", "node", "node_list", "path", "text"}
STATUSES = {"answered", "clarification_needed", "unsupported", "error"}


def _coerce(args: dict, props: dict) -> dict:
    """Drop nulls and fix common type slips (e.g. "5" for an integer)."""
    out = {}
    for k, v in args.items():
        if v is None:
            continue
        t = props.get(k, {}).get("type")
        try:
            if t == "integer" and isinstance(v, (str, float)) and not isinstance(v, bool):
                v = int(float(v))
            elif t == "number" and isinstance(v, str):
                v = float(v)
            elif t == "boolean" and isinstance(v, str):
                v = v.strip().lower() in ("true", "yes", "1")
        except ValueError:
            pass
        out[k] = v
    return out


def _parse_value(raw, answer_type):
    """answer_value arrives as a JSON string; turn it into the right Python type."""
    val = raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            val = raw.strip()
    if answer_type == "number":
        try:
            f = float(str(val).replace(",", "")) if not isinstance(val, (int, float)) else float(val)
            return int(f) if f.is_integer() else f
        except ValueError:
            return val
    if answer_type == "boolean":
        if isinstance(val, str):
            return val.strip().lower() in ("true", "yes")
        return bool(val)
    if answer_type in ("node_list", "path"):
        if isinstance(val, str):
            val = [x.strip().strip("'\"") for x in val.strip("[]").split(",") if x.strip()]
        return [x["id"] if isinstance(x, dict) and "id" in x else x for x in val]
    if answer_type == "node":
        if isinstance(val, list) and len(val) == 1:
            val = val[0]
        if isinstance(val, dict) and "id" in val:
            val = val["id"]
        return val
    return val


class Orchestrator:
    def __init__(self, llm, toolbox: ToolBox | None = None):
        self.llm = llm
        self.toolbox = toolbox or ToolBox()

    def answer(self, question: str, trace: Trace) -> dict:
        store = GraphStore()  # fresh per question: subgraph ids start again at ~1
        messages = [{"role": "system", "content": SYSTEM.format(graphs=graph_catalogue(store))},
                    {"role": "user", "content": question}]
        tools = self.toolbox.schemas() + [FINAL_ANSWER_TOOL]
        trace.question(question)
        used, n_tool_calls, nudged = [], 0, False

        for step in range(MAX_STEPS):
            msg = self.llm.chat(messages, tools)
            trace.llm_call(self.llm.model, msg)
            messages.append(msg)
            calls = msg.get("tool_calls") or []

            if not calls:
                if not nudged:  # the model answered in prose: ask it to use final_answer
                    nudged = True
                    messages.append({"role": "user", "content": "Report your answer by calling final_answer."})
                    continue
                return self._finish(trace, used, "answered", msg.get("content") or "", "text", msg.get("content") or "")

            final = None
            for tc in calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    out = {"status": "error", "error": f"Malformed arguments ({exc}). Send a JSON object."}
                    trace.tool_call(name, {"_raw": tc["function"].get("arguments")}, out)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(out)})
                    continue

                if name == "final_answer":
                    final = args
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": '{"status": "ok"}'})
                    continue

                if name not in self.toolbox.tools:
                    out = {"status": "error", "error": f"Unknown tool '{name}'."}
                elif n_tool_calls >= MAX_TOOL_CALLS:
                    out = {"status": "error", "error": "Tool-call budget used up; answer with what you have."}
                else:
                    n_tool_calls += 1
                    args = _coerce(args, self.toolbox.params(name))
                    try:
                        out = self.toolbox.tools[name].run(store, **args)
                    except TypeError as exc:
                        out = {"status": "error", "result": None, "summary": "", "result_graph_id": None,
                               "error": f"Bad arguments for {name}: {exc}. Check the tool's parameters."}
                    except Exception as exc:
                        out = {"status": "error", "result": None, "summary": "", "result_graph_id": None,
                               "error": f"{name} failed: {type(exc).__name__}: {exc}"}
                    impl = self.toolbox.impl_ids[name]
                    if impl not in used:
                        used.append(impl)
                trace.tool_call(name, args, out)
                text = json.dumps(out, default=str)
                if len(text) > MAX_TOOL_OUTPUT:
                    text = text[:MAX_TOOL_OUTPUT] + '..." (truncated)'
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": text})

            if final is not None:
                status = final.get("status") if final.get("status") in STATUSES else "answered"
                atype = final.get("answer_type") if final.get("answer_type") in ANSWER_TYPES else "text"
                value = _parse_value(final.get("answer_value"), atype)
                return self._finish(trace, used, status, final.get("answer_text", ""), atype, value)

        return self._finish(trace, used, "error", f"No final answer after {MAX_STEPS} steps.", "text", None)

    @staticmethod
    def _finish(trace, used, status, text, atype, value):
        trace.final_answer(text)
        answer = None if status in ("clarification_needed", "unsupported") and value in (None, "", []) else \
            {"type": atype, "value": value}
        if status != "answered" and answer is None:
            answer = {"type": "text", "value": text}
        return {"status": status, "answer_text": text, "answer": answer, "tools_used": used}
