"""Drex client (real + mock).

Real API, per console.nace.ai/docs/drex (read 2026-11-14):
  POST {base}/v1/systemone, Authorization: Bearer nace_sk_...
  body {"model", "state", "questions": {id: {"type": "choice"|"score"|"noul", "instructions", "criteria"}}}
  200  {"model", "answers": {id: {...}}, "usage": {"input_tokens", "output_tokens"}, "evaluation_time_ms", "request_id"}
  429/503/529 -> retry after `retry-after-ms`.
"""
from __future__ import annotations

import hashlib
import json
import time

import httpx

import config
from src.clients import mock_engine as me
from src.clients.base import Answer, DrexResult, estimate_tokens

RETRYABLE = {429, 500, 503, 529}


def _parse_answer(raw: dict) -> Answer:
    t = raw.get("type")
    if t == "choice":
        return Answer(type="choice", probabilities={k: float(v) for k, v in raw["probabilities"].items()},
                      choice=raw.get("choice"), confidence=raw.get("confidence"))
    if t == "score":
        legend = raw.get("legend")
        if isinstance(legend, dict):
            legend = [legend[str(i)] for i in range(len(legend))]
        return Answer(type="score", probabilities={str(k): float(v) for k, v in raw["probabilities"].items()},
                      score=float(raw["score"]), confidence=raw.get("confidence"), legend=legend)
    if t == "noul":
        return Answer(type="noul", p_yes=float(raw["noul"]), confidence=raw.get("confidence"))
    raise ValueError(f"unknown Drex answer type: {t!r}")


class DrexClient:
    name = "drex"

    def __init__(self, model: str | None = None, timeout: float | None = None):
        config.require_real_keys("NACE_API_KEY")
        self.model = model or config.DREX_MODEL
        self._http = httpx.Client(
            base_url=config.NACE_BASE_URL,
            timeout=timeout or config.HTTP_TIMEOUT_S,
            headers={"Authorization": f"Bearer {config.NACE_API_KEY}", "X-Nace-Legend": "array"},
        )

    def evaluate(self, state: str, questions: dict[str, dict], images: list[str] | None = None) -> DrexResult:
        body: dict = {"model": self.model, "state": state, "questions": questions}
        if images:
            body["images"] = images
        t0 = time.perf_counter()
        for attempt in range(3):
            r = self._http.post("/v1/systemone", json=body)
            if r.status_code == 200:
                break
            if r.status_code in RETRYABLE and attempt < 2:
                wait_ms = float(r.headers.get("retry-after-ms") or 1000 * (attempt + 1))
                time.sleep(min(wait_ms, 30000) / 1000)
                continue
            try:
                err = r.json().get("error", {})
            except json.JSONDecodeError:
                err = {"message": r.text[:200]}
            raise RuntimeError(f"Drex {r.status_code} {err.get('type')}: {err.get('message')} "
                               f"issues={err.get('issues')}")
        latency = time.perf_counter() - t0
        data = r.json()
        return DrexResult(
            answers={qid: _parse_answer(a) for qid, a in data["answers"].items()},
            input_tokens=int(data.get("usage", {}).get("input_tokens", 0)),
            latency_s=latency,
            model=data.get("model", self.model),
            server_ms=data.get("evaluation_time_ms"),
            request_id=data.get("request_id"),
        )


class MockDrexClient:
    """Deterministic fake Drex. Simulated latency ~120-260 ms, noisier than mock Claude."""
    name = "drex"
    NOISE = 0.9

    def __init__(self, model: str = "mock-drex"):
        self.model = model

    def evaluate(self, state: str, questions: dict[str, dict], images: list[str] | None = None) -> DrexResult:
        seed = hashlib.sha1(state.encode()).hexdigest()
        answers: dict[str, Answer] = {}
        for qid, q in questions.items():
            s = f"{seed}:{qid}"
            if q["type"] == "choice":
                probs = me.choice(state, list(q["criteria"].keys()), s, self.NOISE)
                top = max(probs, key=probs.get)
                answers[qid] = Answer("choice", probs, choice=top, confidence=probs[top])
            elif q["type"] == "score":
                n = len(q["criteria"])
                if qid in ("fit",):
                    probs = me.relevance(state, n, s, self.NOISE)
                    # dealbreakers / non-target roles drag fit down in the mock
                    pen = me.noul(state, "has_dealbreaker", s, 0.0) - me.noul(state, "is_target_role", s, 0.0)
                    shift = {str(i): p * (1.0 - 0.5 * pen * (i / (n - 1))) for i, p in probs.items()}
                    z = sum(shift.values())
                    probs = {k: v / z for k, v in shift.items()}
                else:
                    probs = me.relevance(state, n, s, self.NOISE)
                exp = sum(int(k) * v for k, v in probs.items())
                answers[qid] = Answer("score", probs, score=exp, confidence=max(probs.values()),
                                      legend=list(q["criteria"]))
            elif q["type"] == "noul":
                p = me.noul(state, qid, s, self.NOISE)
                answers[qid] = Answer("noul", p_yes=p, confidence=max(p, 1 - p))
            else:
                raise ValueError(f"bad question type {q['type']}")
        q_tokens = estimate_tokens(json.dumps(questions))
        sim_latency = 0.12 + (int(seed[:4], 16) / 0xFFFF) * 0.14
        return DrexResult(answers, estimate_tokens(state) + q_tokens, sim_latency, self.model, sim_latency * 800)
