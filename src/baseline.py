"""Claude-only baseline: same profile, same inputs, same output schema as the Drex path."""
from __future__ import annotations

from src.triage import (ACTION_DESC, ACTIONS, CATEGORIES, CATEGORY_DESC, RELEVANCE, build_state, priority)

TRIAGE_SYSTEM = (
    "You triage email for one specific user described in PROFILE. Judge every email from that user's point of view.\n"
    "Categories:\n" + "\n".join(f"- {k}: {v}" for k, v in CATEGORY_DESC.items()) +
    "\nActions:\n" + "\n".join(f"- {k}: {v}" for k, v in ACTION_DESC.items()) +
    "\nRelevance to the user's goals: none, low, medium, high.\nAnswer with the JSON object only."
)
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "relevance": {"type": "string", "enum": RELEVANCE},
        "action": {"type": "string", "enum": ACTIONS},
    },
    "required": ["category", "relevance", "action"],
    "additionalProperties": False,
}

JOB_SYSTEM = (
    "You score job postings for one specific user described in PROFILE.\n"
    "- fit: low, medium or high match to the user's target roles, seniority, stack and location.\n"
    "- is_target_role: true if it is an internship or new-grad/entry-level role in the user's target areas.\n"
    "- has_dealbreaker: true if it requires something the profile says the user cannot meet (security clearance, "
    "citizenship, many years of experience, no visa sponsorship).\nAnswer with the JSON object only."
)
JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "string", "enum": ["low", "medium", "high"]},
        "is_target_role": {"type": "boolean"},
        "has_dealbreaker": {"type": "boolean"},
    },
    "required": ["fit", "is_target_role", "has_dealbreaker"],
    "additionalProperties": False,
}


def _onehot(labels: list[str], pick: str) -> dict[str, float]:
    return {k: 1.0 if k == pick else 0.0 for k in labels}


def triage_email_claude(profile_text: str, email: dict, claude) -> dict:
    r = claude.judge(TRIAGE_SYSTEM, build_state(profile_text, email), TRIAGE_SCHEMA, max_tokens=2048)
    d = r.data
    rel = float(RELEVANCE.index(d["relevance"]))
    return {
        "id": email["id"], "system": "claude",
        "category": d["category"], "category_probs": _onehot(CATEGORIES, d["category"]),
        "action": d["action"], "action_probs": _onehot(ACTIONS, d["action"]),
        "relevance": rel, "confidence": 1.0,
        "priority": priority(float(d["category"] == "urgent_action_needed"), float(d["category"] == "important"), rel),
        "latency_s": r.latency_s, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "model": r.model,
    }


def score_job_claude(profile_text: str, job: dict, claude) -> dict:
    from src.jobs import job_rank_score, job_state

    r = claude.judge(JOB_SYSTEM, job_state(profile_text, job), JOB_SCHEMA, max_tokens=1024)
    d = r.data
    fit = float(["low", "medium", "high"].index(d["fit"]))
    return {
        "fit": fit, "p_target": float(d["is_target_role"]), "p_dealbreaker": float(d["has_dealbreaker"]),
        "confidence": 1.0, "rank_score": job_rank_score(fit, float(d["is_target_role"]), float(d["has_dealbreaker"])),
        "latency_s": r.latency_s, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "system": "claude",
    }
