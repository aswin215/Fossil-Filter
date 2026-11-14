"""Claude client (real + mock): JSON-schema judgments, summaries, profile writing.

Note: current Claude models reject `temperature` (API version 2026-10-21+), so the prompt's
"temperature 0" is replaced by structured outputs (output_config.format json_schema) + low effort,
which is the closest deterministic equivalent available.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import config
from src.clients import mock_engine as me
from src.clients.base import LLMResult, estimate_tokens


class ClaudeClient:
    name = "claude"

    def __init__(self, model: str | None = None):
        config.require_real_keys("ANTHROPIC_API_KEY", "CLAUDE_MODEL")
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0, max_retries=2)
        self.model = model or config.CLAUDE_MODEL

    def _create(self, system: str, user: str, max_tokens: int, schema: dict | None) -> tuple[str, object, float]:
        kwargs: dict = dict(model=self.model, max_tokens=max_tokens, system=system,
                            messages=[{"role": "user", "content": user}])
        oc: dict = {"effort": "low"}
        if schema is not None:
            oc["format"] = {"type": "json_schema", "schema": schema}
        kwargs["output_config"] = oc
        t0 = time.perf_counter()
        resp = self.client.messages.create(**kwargs)
        latency = time.perf_counter() - t0
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude refused the request")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Claude hit max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return text, resp.usage, latency

    def judge(self, system: str, user: str, schema: dict, max_tokens: int = 2048) -> LLMResult:
        text, usage, lat = self._create(system, user, max_tokens, schema)
        return LLMResult(json.loads(text), usage.input_tokens, usage.output_tokens, lat, self.model)

    def write(self, system: str, user: str, max_tokens: int = 1024) -> LLMResult:
        text, usage, lat = self._create(system, user, max_tokens, None)
        return LLMResult(text.strip(), usage.input_tokens, usage.output_tokens, lat, self.model)


class MockClaudeClient:
    """Deterministic fake Claude: same heuristic engine as mock Drex but less noise, slower (1.5-3.5 s)."""
    name = "claude"
    NOISE = 0.45

    def __init__(self, model: str = "mock-claude"):
        self.model = model

    def _lat(self, seed: str) -> float:
        return 1.5 + (int(seed[:4], 16) / 0xFFFF) * 2.0

    def judge(self, system: str, user: str, schema: dict, max_tokens: int = 2048) -> LLMResult:
        seed = hashlib.sha1((system + user).encode()).hexdigest()
        out: dict = {}
        props = schema.get("properties", {})
        for key, spec in props.items():
            s = f"{seed}:{key}"
            if "enum" in spec:
                labels = spec["enum"]
                if key in ("relevance", "fit"):
                    probs = me.relevance(user, len(labels), s, self.NOISE)
                    idx = max(probs, key=probs.get)
                    if key == "fit":
                        pen = me.noul(user, "has_dealbreaker", s, 0.0)
                        idx = str(max(0, int(idx) - (1 if pen > 0.5 else 0)))
                    out[key] = labels[int(idx)]
                else:
                    probs = me.choice(user, labels, s, self.NOISE)
                    out[key] = max(probs, key=probs.get)
            elif spec.get("type") == "boolean":
                out[key] = me.noul(user, key, s, self.NOISE) > 0.5
            elif spec.get("type") == "number":
                out[key] = round(0.6 + (int(seed[5:9], 16) / 0xFFFF) * 0.39, 2)
            else:
                out[key] = ""
        return LLMResult(out, estimate_tokens(system + user), 60, self._lat(seed), self.model)

    def write(self, system: str, user: str, max_tokens: int = 1024) -> LLMResult:
        seed = hashlib.sha1((system + user).encode()).hexdigest()
        if "profile" in system.lower():
            text = "Profile (mock-generated): " + re.sub(r"\s+", " ", user)[:1200]
        else:
            body = user.split("Body:", 1)[-1]
            sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", body)) if len(s.strip()) > 20]
            text = " ".join(sents[:2]) or "No summary available."
        return LLMResult(text, estimate_tokens(system + user), estimate_tokens(text), self._lat(seed), self.model)
