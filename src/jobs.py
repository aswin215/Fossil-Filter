"""LinkedIn alerts: NDI splits + extracts jobs, Drex scores each job against the profile."""
from __future__ import annotations

import re

from src.clients.base import q_bool, q_score

FIT_LEVELS = ["low", "medium", "high"]
JOB_QUESTIONS = {
    "fit": q_score("How well does this JOB fit the user described in PROFILE (target roles, seniority, stack, "
                   "location)?",
                   ["low: wrong role, seniority or field for this user", "medium: partially matches the targets",
                    "high: squarely matches the user's target roles, seniority and stack"]),
    "is_target_role": q_bool("Is this JOB an internship or new-grad / entry-level role in the target areas listed "
                             "in PROFILE?"),
    "has_dealbreaker": q_bool("Does this JOB require something PROFILE says the user cannot meet (security "
                              "clearance, citizenship, many years of experience, no visa sponsorship)?"),
}


def job_state(profile_text: str, job: dict) -> str:
    stack = ", ".join(job.get("tech_stack") or [])
    return (f"PROFILE:\n{profile_text}\n\nJOB:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
            f"Location: {job.get('location')}\nSeniority: {job.get('seniority')}\n"
            f"Employment type: {job.get('employment_type')}\nTech stack: {stack}\n"
            f"Notes: {job.get('sponsorship_or_clearance_notes') or 'none'}")


def job_rank_score(fit_expected: float, p_target: float, p_dealbreaker: float) -> float:
    """Expected fit (0..1), pushed down by dealbreakers, nudged up by target-role match."""
    return round((fit_expected / 2) * (1 - 0.7 * p_dealbreaker) + 0.15 * p_target, 4)


def norm_key(company: str | None, title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", f"{company or ''}|{title or ''}".lower()).strip()


def extract_jobs(email: dict, ndi) -> tuple[list[dict], dict]:
    r = ndi.extract_jobs(email)
    jobs = []
    for j in r.jobs:
        d = dict(j.fields)
        d.update({"email_id": email["id"], "grounding": j.grounding, "extract_confidence": j.confidence})
        jobs.append(d)
    return jobs, {"pages": r.pages, "latency_s": r.latency_s, "n_jobs": len(jobs)}


def score_job_drex(profile_text: str, job: dict, drex) -> dict:
    r = drex.evaluate(job_state(profile_text, job), JOB_QUESTIONS, purpose="job_fit")
    fit, tgt, deal = r.answers["fit"], r.answers["is_target_role"], r.answers["has_dealbreaker"]
    return {
        "fit": fit.score, "fit_probs": fit.probabilities, "p_target": tgt.p_yes, "p_dealbreaker": deal.p_yes,
        "confidence": min(fit.top_prob, tgt.top_prob, deal.top_prob), "fit_entropy": fit.entropy_norm,
        "rank_score": job_rank_score(fit.score, tgt.p_yes, deal.p_yes),
        "latency_s": r.latency_s, "input_tokens": r.input_tokens, "output_tokens": 0, "system": "drex",
        "drex_cost_usd": r.cost_usd,
    }


def rank_jobs(scored: list[dict], top_n: int | None = None) -> list[dict]:
    """Dedupe by company+title (keep best score), sort by rank_score."""
    best: dict[str, dict] = {}
    for j in scored:
        k = norm_key(j.get("company"), j.get("title"))
        if k not in best or j["score"]["rank_score"] > best[k]["score"]["rank_score"]:
            best[k] = j
    out = sorted(best.values(), key=lambda j: j["score"]["rank_score"], reverse=True)
    return out[:top_n] if top_n else out
