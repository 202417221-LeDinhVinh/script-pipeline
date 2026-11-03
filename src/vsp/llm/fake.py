"""Fake LLM client for offline tests: fixture JSON keyed by prompt name + call order."""
from __future__ import annotations
import re
from .client import Usage


def load_scripts_file(path) -> dict:
    """Load fixture scripts from JSON: keys 'role:n' (call order) or 'role:tag' (substring)."""
    import json as _json
    from pathlib import Path as _Path
    data = _json.loads(_Path(path).read_text(encoding="utf-8"))
    out = {}
    for k, v in data.items():
        role, _, rest = k.partition(":")
        try:
            n = int(rest)
        except ValueError:
            n = rest
        out[(role, n)] = v
    return out


class FakeLLMClient:
    def __init__(self, scripts: dict | None = None, budget=None, cfg=None,
                 chunk_id: str | None = None, stage: str | None = None):
        # scripts: {(role, nth_call_for_role): response_object_or_str}
        self.scripts = scripts or {}
        self.counters: dict = {}
        self.calls: list = []
        self.budget = budget
        self.cfg = cfg
        self.chunk_id = chunk_id
        self.stage = stage

    def bind(self, budget=None, cfg=None, chunk_id: str | None = None, stage: str | None = None):
        if budget is not None:
            self.budget = budget
        if cfg is not None:
            self.cfg = cfg
        if chunk_id is not None:
            self.chunk_id = chunk_id
        if stage is not None:
            self.stage = stage
        return self

    def _next(self, role: str):
        n = self.counters.get(role, 0) + 1
        self.counters[role] = n
        return n

    def _max_tokens_for(self, role: str) -> int:
        try:
            if self.cfg is not None:
                return int(self.cfg.pipeline["models"][role].get("max_tokens", 1500))
        except Exception:
            pass
        return 1500

    def _model_for(self, role: str) -> str:
        try:
            if self.cfg is not None:
                return str(self.cfg.pipeline["models"][role].get("model_id", f"fake-{role}"))
        except Exception:
            pass
        return f"fake-{role}"

    def _precheck(self, role: str, messages: list) -> None:
        if self.budget is None:
            return
        self.budget.precheck(role, messages, self._max_tokens_for(role))

    def _record(self, role: str, messages: list, latency: float = 0.01) -> Usage:
        import hashlib
        import json as _json
        if self.budget is None:
            return Usage(100, 100, 0.0, False)
        inp, outp = 100, 100
        cost = 0.0
        try:
            if self.cfg is not None:
                from .client import model_prices
                pi, po = model_prices(self.cfg, role)
                cost = inp / 1e6 * pi + outp / 1e6 * po
        except Exception:
            cost = 0.0
        prompt_hash = hashlib.sha256(
            _json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()
        usage = Usage(inp, outp, cost, False)
        self.budget.record(role, self._model_for(role),
                           usage, prompt_hash, latency,
                           chunk_id=self.chunk_id, stage=self.stage)
        return usage

    def _lookup(self, role: str, messages: list):
        n = self._next(role)
        self.calls.append({"role": role, "n": n, "messages": messages})
        key = (role, n)
        if key in self.scripts:
            return self.scripts[key]
        # fallback: find by prompt-name tag in system message
        blob = " ".join(m.get("content", "") for m in messages)
        for (r, tag), val in list(self.scripts.items()):
            if r == role and isinstance(tag, str) and tag in blob:
                return val
        # C1 migration aid (test code only, never production): legacy suites
        # provide a single judge fixture per round. Panel needs 4 samples +
        # 1 fidelity call per round (n%5: 1-4 panel, 0 fidelity). Reuse the
        # round's first judge fixture for missing panel slots and return a
        # default supported fidelity claim for missing fidelity slots, so old
        # decision-table tests exercise the checklist path without editing
        # every fixture by hand. New tests provide explicit 4+1 fixtures.
        if role == "judge":
            base = ((n - 1) // 5) * 5 + 1
            first = self.scripts.get((role, base))
            if first is not None and isinstance(first, dict) and "items" in first:
                if n % 5 != 0:
                    return first
                return {"claims": [{"claim": "x", "verdict": "supported",
                                    "source_paragraph_ids": [0]}]}
        raise AssertionError(f"FakeLLMClient: no fixture for role={role} call#{n}")

    def chat(self, role: str, messages: list, chunk_id: str | None = None, stage: str | None = None):
        import json
        if chunk_id is not None:
            self.chunk_id = chunk_id
        if stage is not None:
            self.stage = stage
        self._precheck(role, messages)
        resp = self._lookup(role, messages)
        usage = self._record(role, messages)
        content = resp if isinstance(resp, str) else json.dumps(resp)
        return content, usage

    def chat_json(self, role: str, messages: list, chunk_id: str | None = None, stage: str | None = None,
                  require_canary: bool = False):
        import json
        if chunk_id is not None:
            self.chunk_id = chunk_id
        if stage is not None:
            self.stage = stage
        self._precheck(role, messages)
        resp = self._lookup(role, messages)
        usage = self._record(role, messages)
        if isinstance(resp, str):
            return json.loads(resp), usage
        return resp, usage
