"""Hybrid: trust Drex when it is confident, escalate low-confidence items to Claude.

Escalate when the top-class probability of category or action is below ESCALATE_THRESHOLD, or when the
relevance score distribution is too flat (normalized entropy above 1 - threshold).
"""
from __future__ import annotations

import config
from src.baseline import score_job_claude, triage_email_claude
from src.triage import email_block

ENTROPY_MAX = 0.92  # a near-uniform relevance distribution means Drex is unsure even if category is peaked

SUMMARY_SYSTEM = ("Summarize this email for a busy user in exactly 2 short sentences: what it is and what, if "
                  "anything, they need to do. No preamble.")


def should_escalate(drex_res: dict, threshold: float) -> bool:
    ent = drex_res.get("relevance_entropy", drex_res.get("fit_entropy", 0.0))
    return drex_res["confidence"] < threshold or ent > ENTROPY_MAX


def hybrid_triage(profile_text: str, email: dict, drex_res: dict, claude, threshold: float | None = None) -> dict:
    t = config.ESCALATE_THRESHOLD if threshold is None else threshold
    if not should_escalate(drex_res, t):
        return {**drex_res, "system": "hybrid", "escalated": False}
    c = triage_email_claude(profile_text, email, claude)
    return {**c, "system": "hybrid", "escalated": True,
            "latency_s": drex_res["latency_s"] + c["latency_s"],
            "drex_input_tokens": drex_res["input_tokens"]}


def hybrid_job(profile_text: str, job: dict, drex_score: dict, claude, threshold: float | None = None) -> dict:
    t = config.ESCALATE_THRESHOLD if threshold is None else threshold
    if drex_score["confidence"] >= t:
        return {**drex_score, "system": "hybrid", "escalated": False}
    c = score_job_claude(profile_text, job, claude)
    return {**c, "system": "hybrid", "escalated": True, "latency_s": drex_score["latency_s"] + c["latency_s"]}


def combine_offline(drex_rows: dict[str, dict], claude_rows: dict[str, dict], threshold: float) -> dict[str, dict]:
    """Simulate the hybrid at any threshold from already-computed Drex + Claude predictions (for the sweep)."""
    out = {}
    for eid, d in drex_rows.items():
        if should_escalate(d, threshold) and eid in claude_rows:
            c = claude_rows[eid]
            out[eid] = {**c, "escalated": True, "latency_s": d["latency_s"] + c["latency_s"],
                        "drex_input_tokens": d["input_tokens"]}
        else:
            out[eid] = {**d, "escalated": False}
    return out


def summarize(email: dict, claude) -> tuple[str, dict]:
    r = claude.write(SUMMARY_SYSTEM, email_block(email), max_tokens=512)
    return r.data, {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "latency_s": r.latency_s}
