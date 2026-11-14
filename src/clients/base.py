"""Shared result types and question builders for Drex (shapes follow console.nace.ai/docs/drex)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------- Drex question builders (wire format verified against the docs) ----------
# The docs name the three types "noul" (yes/no), "choice", and "score".
def q_choice(instructions: str, options: dict[str, str | None]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def q_score(instructions: str, levels_ascending: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels_ascending}


def q_bool(instructions: str, true_desc: str | None = None, false_desc: str | None = None) -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    crit = {k: v for k, v in (("true", true_desc), ("false", false_desc)) if v}
    if crit:
        q["criteria"] = crit
    return q


@dataclass
class Answer:
    """Normalized Drex answer."""
    type: str  # choice | score | noul
    probabilities: dict[str, float] = field(default_factory=dict)  # choice: label->p ; score: level_idx->p
    choice: str | None = None
    score: float | None = None  # expected level index (score)
    p_yes: float | None = None  # noul
    confidence: float | None = None
    legend: list[str] | None = None

    @property
    def top_prob(self) -> float:
        if self.type == "noul":
            return max(self.p_yes, 1 - self.p_yes)
        return max(self.probabilities.values()) if self.probabilities else 0.0

    @property
    def entropy_norm(self) -> float:
        ps = [self.p_yes, 1 - self.p_yes] if self.type == "noul" else list(self.probabilities.values())
        n = len(ps)
        if n <= 1:
            return 0.0
        h = -sum(p * math.log(p) for p in ps if p > 0)
        return h / math.log(n)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class DrexResult:
    answers: dict[str, Answer]
    input_tokens: int
    latency_s: float  # wall-clock (mocks: simulated)
    model: str
    server_ms: float | None = None
    request_id: str | None = None


@dataclass
class LLMResult:
    data: Any  # parsed JSON or text
    input_tokens: int
    output_tokens: int
    latency_s: float
    model: str


@dataclass
class NDIJob:
    fields: dict[str, Any]
    grounding: dict[str, Any] = field(default_factory=dict)  # field -> citation/location
    confidence: float | None = None


@dataclass
class NDIResult:
    jobs: list[NDIJob]
    pages: float
    latency_s: float
    raw_job_id: str | None = None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
