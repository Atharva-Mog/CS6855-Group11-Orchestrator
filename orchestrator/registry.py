"""Discover tools at startup by reading manifests (never hard-coded).

Scans registry/tools/<tool_name>/<implementation_id>/tool.json and infra_tools/*/tool.json.
registry/tools holds only the implementations our curation step chose (see curation/).
"""
from __future__ import annotations

from pathlib import Path

from graphkit import load_tool
from graphkit.llm import tool_schema

ROOT = Path(__file__).resolve().parent.parent


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = cut.rfind(". ")
    return cut[:end + 1] if end > limit // 2 else cut.rstrip() + "..."


class ToolBox:
    def __init__(self, root: Path = ROOT):
        self.tools = {}      # manifest name -> loaded Tool
        self.impl_ids = {}   # manifest name -> implementation_id (for tools_used)
        folders = sorted(p.parent for p in (root / "infra_tools").glob("*/tool.json"))
        folders += sorted(p.parent for p in (root / "registry" / "tools").glob("*/*/tool.json"))
        for folder in folders:
            try:
                tool = load_tool(folder)
            except Exception:
                continue  # a broken folder must not stop the orchestrator
            name = tool.manifest["name"]
            if name in self.tools:
                continue  # one implementation per tool
            self.tools[name] = tool
            self.impl_ids[name] = tool.manifest.get("implementation_id", name)

    def schemas(self) -> list[dict]:
        """Tool definitions for the LLM, trimmed so every request stays small (free-tier token limits)."""
        out = []
        for t in self.tools.values():
            sch = tool_schema(t.manifest)
            fn = sch["function"]
            fn["description"] = _trim(fn["description"], 420)
            for spec in fn["parameters"].get("properties", {}).values():
                if "description" in spec:
                    spec["description"] = _trim(spec["description"], 140)
            out.append(sch)
        return out

    def params(self, name: str) -> dict:
        return self.tools[name].manifest["parameters"].get("properties", {})
