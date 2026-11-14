"""End-to-end inbox run for one profile: guardrail -> Drex triage -> hybrid -> jobs -> summaries.

Result is written to results/cache/inbox_<profile>_<mode>.json for the dashboard.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import config
from src import guardrail
from src.hybrid import hybrid_job, hybrid_triage, summarize
from src.jobs import extract_jobs, rank_jobs, score_job_drex
from src.loader import load_emails
from src.profile import load_profile
from src.triage import triage_email


def _pmap(fn, items, workers):
    if workers <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def mode_tag(clients) -> str:
    return "mock" if clients.mock else "real"


def inbox_cache_path(profile: str, mock: bool):
    return config.CACHE_DIR / f"inbox_{profile}_{'mock' if mock else 'real'}.json"


def run_inbox(profile_name: str, clients, emails: list[dict] | None = None, threshold: float | None = None,
              sensitive_threshold: float | None = None, use_semantic_guard: bool = True, summaries: bool = True,
              workers: int | None = None) -> dict:
    t = config.ESCALATE_THRESHOLD if threshold is None else threshold
    workers = workers or (1 if clients.mock else 4)
    prof = load_profile(profile_name)["profile_text"]
    emails = emails if emails is not None else load_emails()

    kept, rep = guardrail.filter_emails(emails, clients.drex, sensitive_threshold, use_semantic_guard)

    def triage_one(e):
        d = triage_email(prof, e, clients.drex)
        h = hybrid_triage(prof, e, d, clients.claude, t)
        return {**h, "drex": {k: d[k] for k in ("category", "action", "confidence", "priority", "latency_s")}}

    rows = _pmap(triage_one, kept, workers)
    by_id = {e["id"]: e for e in kept}
    for r in rows:
        e = by_id[r["id"]]
        r.update({"from": e["from"], "subject": e["subject"], "date": e.get("date", "")})

    rows.sort(key=lambda r: r["priority"], reverse=True)
    if summaries:
        for r in [r for r in rows if r["category"] in ("urgent_action_needed", "important")][: config.TOP_K_SUMMARIES]:
            try:
                r["summary"], _ = summarize(by_id[r["id"]], clients.claude)
            except Exception as ex:  # summaries are optional
                r["summary"] = f"(summary unavailable: {type(ex).__name__})"

    jobs_all = []
    for e in [e for e in kept if e.get("is_linkedin_alert")]:
        try:
            jobs, _meta = extract_jobs(e, clients.ndi)
        except Exception as ex:
            print(f"[jobs] NDI failed for {e['id']}: {type(ex).__name__}: {ex}")
            continue

        def score_one(j):
            d = score_job_drex(prof, j, clients.drex)
            return {**j, "score": hybrid_job(prof, j, d, clients.claude, t), "drex_score": d}

        jobs_all += _pmap(score_one, jobs, workers)

    out = {
        "profile": profile_name, "mode": mode_tag(clients), "threshold": t,
        "guard": rep.to_dict(), "emails": rows,
        "jobs": rank_jobs(jobs_all, config.TOP_N_JOBS),
        "escalated_frac": (sum(r.get("escalated", False) for r in rows) / len(rows)) if rows else 0.0,
    }
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    inbox_cache_path(profile_name, clients.mock).write_text(json.dumps(out, default=str))
    return out
