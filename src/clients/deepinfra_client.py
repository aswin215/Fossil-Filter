"""DeepInfra client (partner tool), real + mock: a second LLM baseline with the same `judge()` interface as Claude.

DeepInfra serves an OpenAI-compatible API at https://api.deepinfra.com/v1/openai (the same base URL Intern's
provider registry uses for `deepinfra/...`). The model is the Intern agent's System 2, Qwen/Qwen3.8-27B by default.
JSON mode (`response_format: json_object`) + temperature 0; the reply is validated against the schema's enums.
"""
from __future__ import annotations

import json
import re
import time

import httpx

import config
from src.clients.base import LLMResult
from src.clients.claude_client import MockClaudeClient

_THINK = re.compile(r"<think>.*?</think>", re.S)


def _parse_json(text: str) -> dict:
    text = _THINK.sub("", text).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _validate(d: dict, schema: dict) -> dict:
    out = {}
    for k, spec in schema["properties"].items():
        if k not in d:
            raise ValueError(f"missing key {k!r}")
        v = d[k]
        if isinstance(v, list) and len(v) == 1:  # Qwen sometimes answers ["label"]
            v = v[0]
        if "enum" in spec:
            v = str(v).strip().lower()
            if v not in spec["enum"]:
                raise ValueError(f"{k}={v!r} not in enum")
        elif spec.get("type") == "boolean":
            v = v if isinstance(v, bool) else str(v).strip().lower() in {"true", "yes", "1"}
        out[k] = v
    return out


class DeepInfraClient:
    name = "deepinfra"

    def __init__(self, model: str | None = None):
        config.require_real_keys("DEEPINFRA_API_KEY")
        self.model = model or config.DEEPINFRA_MODEL
        self._http = httpx.Client(base_url=config.DEEPINFRA_BASE_URL, timeout=90.0,
                                  headers={"Authorization": f"Bearer {config.DEEPINFRA_API_KEY}"})

    def _chat(self, system: str, user: str, max_tokens: int, json_mode: bool) -> tuple[str, dict, float]:
        body = {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        for attempt in range(3):
            r = self._http.post("/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(1.5 * 2 ** attempt)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"DeepInfra {r.status_code}: {r.text[:300]}")
            break
        lat = time.perf_counter() - t0
        data = r.json()
        return data["choices"][0]["message"].get("content") or "", data.get("usage") or {}, lat

    def judge(self, system: str, user: str, schema: dict, max_tokens: int = 4096) -> LLMResult:
        keys = "; ".join(f'"{k}": one string from {" | ".join(v["enum"])}' if "enum" in v else f'"{k}": {v.get("type")}'
                         for k, v in schema["properties"].items())
        sys = (system + "\nReply with ONE flat JSON object, each value a single string (not a list): {" + keys + "}")
        tot_in = tot_out = 0
        lat_sum, cost = 0.0, 0.0
        for attempt in range(2):  # one retry on an empty or invalid reply
            text, usage, lat = self._chat(sys, user, max_tokens, json_mode=True)
            tot_in += int(usage.get("prompt_tokens", 0))
            tot_out += int(usage.get("completion_tokens", 0))
            cost += float(usage.get("estimated_cost") or 0.0)
            lat_sum += lat
            try:
                data = _validate(_parse_json(text), schema)
                break
            except (ValueError, json.JSONDecodeError):
                if attempt == 1:
                    raise
        r = LLMResult(data, tot_in, tot_out, lat_sum, self.model)
        r.cost_usd = cost
        return r

    def write(self, system: str, user: str, max_tokens: int = 512) -> LLMResult:
        text, usage, lat = self._chat(system, user, max_tokens, json_mode=False)
        return LLMResult(_THINK.sub("", text).strip(), int(usage.get("prompt_tokens", 0)),
                         int(usage.get("completion_tokens", 0)), lat, self.model)


class MockDeepInfraClient(MockClaudeClient):
    """Deterministic fake Qwen-on-DeepInfra: between mock Drex and mock Claude in noise, ~0.8-2.0 s."""
    name = "deepinfra"
    NOISE = 0.7

    def __init__(self, model: str = "mock-qwen"):
        self.model = model

    def _lat(self, seed: str) -> float:
        return 0.8 + (int(seed[:4], 16) / 0xFFFF) * 1.2

    def judge(self, system: str, user: str, schema: dict, max_tokens: int = 2048) -> LLMResult:
        return super().judge("qwen:" + system, user, schema, max_tokens)
