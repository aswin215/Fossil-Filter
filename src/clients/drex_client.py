"""Drex client: a thin adapter over Intern's decision client (`intern.decisions`), real + mock.

Every Drex call in the batch pipeline goes through `intern.decisions.DecisionClient`, so retries, 429 handling,
model failover, the answer cache, concurrency limits and `decision` events come from Nace's harness, not from us.

Wire format (verified in Intern's src/intern/decisions.py and console.nace.ai/docs/drex):
  POST {base_url}/systemone   base_url = https://console.nace.ai/v1   Authorization: Bearer $NACE_API_KEY
  {"model", "state", "questions": {name: {"type": "choice"|"score"|"noul", "instructions", "criteria"?}}}
  -> {"answers": {name: answer}, "usage": {...}}
Intern normalizes a score answer to 0..1 with a per-level probability list; this adapter converts it back to the
expected level index (0..n-1) that the rest of the project uses.

Mock mode keeps the same path: a deterministic fake `DecisionBackend` sits behind the real Intern
`DecisionClient`, so the harness (cache, events, concurrency) is exercised even without keys.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections import deque

from pydantic import SecretStr

from intern.decisions import (Answers, ChoiceAnswer, ChoiceQuestion, DecisionClient, DecisionsApiBackend,
                              NoulAnswer, NoulQuestion, ScoreAnswer, ScoreQuestion)

import config
from src.clients import mock_engine as me
from src.clients.base import Answer, DrexResult, estimate_tokens

# ------------------------------------------------------------------ one background event loop
# Intern's client is async; our pipeline is a sync thread pool. All asks run on one long-lived loop so Intern's
# pooled HTTP client, semaphore and cache are shared across threads (and across Streamlit reruns).
_LOOP: asyncio.AbstractEventLoop | None = None
_LOCK = threading.Lock()


def _loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    with _LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, name="intern-decisions", daemon=True).start()
    return _LOOP


def _run(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop()).result()


# ------------------------------------------------------------------ question / answer conversion
def to_intern(q: dict):
    t = q["type"]
    if t == "choice":
        return ChoiceQuestion(instructions=q["instructions"],
                              criteria={k: (v or k.replace("_", " ")) for k, v in q["criteria"].items()})
    if t == "score":
        return ScoreQuestion(instructions=q["instructions"], criteria=list(q["criteria"]))
    if t == "noul":
        return NoulQuestion(instructions=q["instructions"])
    raise ValueError(f"unknown question type {t!r}")


def from_intern(a, q: dict) -> Answer:
    if isinstance(a, ChoiceAnswer):
        probs = {k: float(v) for k, v in a.probabilities.items()} or {a.choice: 1.0}
        return Answer("choice", probs, choice=a.choice, confidence=a.confidence or probs.get(a.choice))
    if isinstance(a, ScoreAnswer):
        n = len(q["criteria"])
        idx = a.score * (n - 1)  # Intern: 0..1 -> our expected level index 0..n-1
        probs = ({str(i): float(p) for i, p in enumerate(a.probabilities)} if a.probabilities
                 else {str(i): float(i == round(idx)) for i in range(n)})
        return Answer("score", probs, score=idx, confidence=a.confidence or max(probs.values()),
                      legend=a.legend or list(q["criteria"]))
    if isinstance(a, NoulAnswer):
        return Answer("noul", p_yes=float(a.noul), confidence=max(a.noul, 1 - a.noul))
    raise ValueError(f"unexpected answer {a!r}")


# ------------------------------------------------------------------ mock System 1 backend
class MockDrexBackend:
    """Deterministic fake Drex implementing Intern's `DecisionBackend` protocol. Noisier than mock Claude.
    Latency is simulated (~120-260 ms) and reported, not slept, so mock evals stay fast."""
    NOISE = 0.9

    def __init__(self, model: str = "mock-drex"):
        self.name = f"mock:{model}"
        self.model = model

    @staticmethod
    def simulated_latency(state: str) -> float:
        return 0.12 + (int(hashlib.sha1(state.encode()).hexdigest()[:4], 16) / 0xFFFF) * 0.14

    async def decide(self, state: str, questions: dict) -> Answers:
        seed = hashlib.sha1(state.encode()).hexdigest()
        out: dict = {}
        for qid, q in questions.items():
            s = f"{seed}:{qid}"
            if isinstance(q, ChoiceQuestion):
                probs = me.choice(state, list(q.criteria.keys()), s, self.NOISE)
                top = max(probs, key=probs.get)
                out[qid] = ChoiceAnswer(choice=top, probabilities=probs, confidence=probs[top])
            elif isinstance(q, ScoreQuestion):
                n = len(q.criteria)
                probs = me.relevance(state, n, s, self.NOISE)
                if qid == "fit":  # dealbreakers / non-target roles drag fit down in the mock
                    pen = me.noul(state, "has_dealbreaker", s, 0.0) - me.noul(state, "is_target_role", s, 0.0)
                    shift = {i: p * (1.0 - 0.5 * pen * (int(i) / (n - 1))) for i, p in probs.items()}
                    z = sum(shift.values())
                    probs = {k: v / z for k, v in shift.items()}
                plist = [probs[str(i)] for i in range(n)]
                exp = sum(i * p for i, p in enumerate(plist))
                out[qid] = ScoreAnswer(score=exp / (n - 1), probabilities=plist, legend=list(q.criteria),
                                       confidence=max(plist))
            else:
                out[qid] = NoulAnswer(noul=me.noul(state, qid, s, self.NOISE))
        q_tokens = estimate_tokens(json.dumps({k: v.model_dump() for k, v in questions.items()}))
        return Answers(answers=out, input_tokens=estimate_tokens(state) + q_tokens)


# ------------------------------------------------------------------ the adapter
def make_backend(model: str):
    """Console (`drex-*`, key NACE_API_KEY) or OpenRouter's decisions API (`decisions:nace-ai/drex-max`)."""
    if model.startswith("decisions:"):
        config.require_real_keys("OPENROUTER_API_KEY")
        models = model.removeprefix("decisions:").split(",")
        return DecisionsApiBackend(config.OPENROUTER_DECISIONS_URL, SecretStr(config.OPENROUTER_API_KEY), models,
                                   timeout_s=config.HTTP_TIMEOUT_S)
    config.require_real_keys("NACE_API_KEY")
    return DecisionsApiBackend(config.NACE_BASE_URL, SecretStr(config.NACE_API_KEY), model.split(","),
                               timeout_s=config.HTTP_TIMEOUT_S)


class DrexClient:
    """Sync facade used by triage/jobs/guardrail. `evaluate(state, questions)` -> DrexResult."""
    name = "drex"

    def __init__(self, model: str | None = None, backend=None, retry_window_s: float = 30.0,
                 max_concurrency: int = 16):
        self.model = model or config.DREX_MODEL
        self.backend = backend or make_backend(self.model)
        self.mock = isinstance(self.backend, MockDrexBackend)
        # Decision events (purpose, ok, cached, duration, tokens, cost, answers) - never the state text.
        self.events: deque = deque(maxlen=20_000)
        self._by_subject: dict[str, object] = {}
        self.decisions = DecisionClient(self.backend, self._on_event, timeout_s=config.HTTP_TIMEOUT_S,
                                        max_state_tokens=32_768, max_concurrency=max_concurrency,
                                        retry_window_s=retry_window_s)

    def _on_event(self, ev) -> None:
        self.events.append(ev)
        if ev.ok and ev.subject:
            self._by_subject[ev.subject] = ev

    def evaluate(self, state: str, questions: dict[str, dict], purpose: str = "inbox-predator") -> DrexResult:
        iq = {k: to_intern(q) for k, q in questions.items()}
        subject = f"{purpose}:{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()
        answers = _run(self.decisions.ask(purpose, state, iq, subject=subject))
        wall = time.perf_counter() - t0
        ev = self._by_subject.pop(subject, None)
        cached = bool(getattr(ev, "cached", False))
        in_tok = int(getattr(ev, "input_tokens", 0) or 0)
        if self.mock:
            wall = 0.0005 if cached else self.backend.simulated_latency(state)
            if cached:
                in_tok = 0
        return DrexResult(
            answers={k: from_intern(a, questions[k]) for k, a in answers.items()},
            input_tokens=in_tok,
            latency_s=wall,
            model=self.model,
            server_ms=getattr(ev, "duration_ms", None),
            cost_usd=float(getattr(ev, "cost_usd", 0.0) or 0.0),
            cached=cached,
        )

    def usage(self) -> dict:
        u = self.decisions.usage
        evs = [e for e in self.events if e.purpose != "check"]
        dur = sorted(e.duration_ms for e in evs if e.ok and not e.cached)
        return {"backend": self.backend.name, "asks": u.asks, "failed": u.failed,
                "input_tokens": u.input_tokens, "cost_usd": u.cost_usd,
                "events": len(evs), "cached": sum(e.cached for e in evs), "errors": sum(not e.ok for e in evs),
                "p50_ms": dur[len(dur) // 2] if dur else None}


def MockDrexClient(model: str = "mock-drex") -> DrexClient:  # noqa: N802 - kept as the old factory name
    return DrexClient(model=model, backend=MockDrexBackend(model))
