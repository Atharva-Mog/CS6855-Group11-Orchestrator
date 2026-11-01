"""LLM calls with an on-disk cache, so re-running the same questions does not spend quota."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from graphkit.llm import make_client, model_name

CACHE_DIR = Path(__file__).resolve().parent.parent / ".llm_cache"


class LLM:
    def __init__(self, use_cache: bool = True):
        self.client = make_client()
        self.model = model_name()
        self.use_cache = use_cache
        # Optional pacing for free-tier keys: minimum seconds between LLM requests (LLM_MIN_INTERVAL).
        self.min_interval = float(os.environ.get("LLM_MIN_INTERVAL", "0") or 0)
        self._last = 0.0

    def chat(self, messages: list, tools: list) -> dict:
        """Returns the assistant message as a plain dict (provider extras kept)."""
        key = hashlib.sha256(json.dumps([self.model, messages, tools], sort_keys=True, default=str).encode()).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        if self.use_cache and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        delay = 10
        for attempt in range(8):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                resp = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                           temperature=0)
                break
            except Exception as exc:  # rate limits / transient errors: back off and retry
                text = str(exc)
                transient = any(s in text for s in ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED",
                                                    "overloaded", "timeout", "Timeout", "Connection"))
                if attempt == 7 or not transient:
                    raise
                hint = re.search(r"retry(?:Delay| in)[^0-9]*([0-9.]+)", text)  # provider's suggested wait
                time.sleep(float(hint.group(1)) + 1 if hint else delay)
                delay = min(delay * 2, 90)
        msg = resp.choices[0].message.model_dump(exclude_none=True)
        msg["role"] = "assistant"
        msg.setdefault("content", "")
        if msg["content"] is None:
            msg["content"] = ""
        if self.use_cache:
            CACHE_DIR.mkdir(exist_ok=True)
            path.write_text(json.dumps(msg), encoding="utf-8")
        return msg
