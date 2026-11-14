"""Level B: an Intern agent session inside the product.

Launches Nace's harness headlessly:
    intern -p --json --system2 deepinfra/Qwen/Qwen3.8-27B --system1 drex-latest --cwd data/agent_workspace ...
System 2 (Qwen on DeepInfra, a partner tool) does the triage work with Intern's file tools; System 1 (Drex) judges
every turn (stuck detection, tool-call guard, compaction). The agent writes agent_triage.json in our schema.

Privacy: only emails that PASSED the guardrail are copied into the agent workspace (gitignored), the session runs
with --no-session --no-memory so no journal of mail content is kept, and the saved event log
(results/agent_events.jsonl) keeps only ids, types, names, timings, costs and System 1's probabilities - never text
deltas, tool outputs, tool arguments or the final reply.

Mock mode cannot run Intern (it refuses to start without a reachable System 1), so it SIMULATES a session: the
mock Qwen judges each email and a synthetic event stream is produced. It is labeled `simulated` everywhere.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import config
from src import guardrail
from src.baseline import triage_email_claude
from src.profile import load_profile
from src.triage import ACTION_DESC, ACTIONS, CATEGORIES, CATEGORY_DESC, RELEVANCE, priority

AGENT_OUT = config.RESULTS_DIR / "agent_triage.json"
EVENTS_OUT = config.RESULTS_DIR / "agent_events.jsonl"
AGENT_BODY_CHARS = 1500

# Event fields that are safe to keep (no email content can appear in them).
SAFE_FIELDS = {
    "run_start": ["session_id", "model", "tools"],
    "turn_start": ["turn"],
    "tool_call_start": ["turn", "name", "call_id"],
    "tool_call_end": ["turn", "name", "call_id", "status", "duration_ms"],
    "decision": ["purpose", "backend", "questions", "ok", "cached", "cost_usd", "input_tokens", "duration_ms",
                 "turn", "answers", "error", "truncated"],
    "nudge": ["turn", "reason"],
    "tool_guard": ["turn", "name", "layer", "rule", "risk", "asked"],
    "compaction": ["turn", "method", "tokens_before", "tokens_after", "turns_compacted", "kept"],
    "context_budget": ["turn", "tokens", "working_window"],
    "run_end": ["reason", "turns", "usage", "agent_usage", "system1", "duration_ms"],
}

TASK = """You are triaging email for one user. Work only inside the current folder.

Files:
- profile.txt: who the user is and what matters to them.
- emails.jsonl: {n} emails, one JSON object per line with keys id, from, subject, date, body.
- schema.md: the labels you must use.

For EVERY email decide:
- category: one of {categories}
- relevance: one of {relevance} (relevance to this user's goals)
- action: one of {actions}

Read the emails yourself in chunks of about 15 lines and judge each one from the user's point of view. Do not
write a keyword-matching script to label them. After each chunk, append your decisions to agent_triage.json so
the file always holds a JSON list of objects {{"id", "category", "relevance", "action"}}. When all {n} ids are in
agent_triage.json, check that it parses as JSON and has {n} entries, then reply DONE.
Never send, delete or modify email, never use the network, and write no file other than agent_triage.json."""


def _schema_md() -> str:
    return ("# Labels\n\n## category\n" + "\n".join(f"- `{k}`: {v}" for k, v in CATEGORY_DESC.items()) +
            "\n\n## relevance\n" + ", ".join(f"`{r}`" for r in RELEVANCE) +
            "\n\n## action\n" + "\n".join(f"- `{k}`: {v}" for k, v in ACTION_DESC.items()) + "\n")


def prepare_workspace(profile_name: str, emails: list[dict], drex, sensitive_threshold: float | None = None,
                      limit: int | None = None) -> tuple[Path, list[dict], dict]:
    """Guardrail first (fail closed), then copy only the safe emails into the agent workspace."""
    kept, rep = guardrail.filter_emails(emails, drex, sensitive_threshold or config.SENSITIVE_THRESHOLD,
                                        use_semantic=True, audit=True)
    kept = kept[:limit] if limit else kept
    ws = config.AGENT_WORKSPACE
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    (ws / "profile.txt").write_text(load_profile(profile_name)["profile_text"])
    (ws / "schema.md").write_text(_schema_md())
    with open(ws / "emails.jsonl", "w") as f:
        for e in kept:
            f.write(json.dumps({"id": e["id"], "from": e["from"], "subject": e["subject"], "date": e["date"],
                                "body": e.get("body_text", "")[:AGENT_BODY_CHARS]}, ensure_ascii=False) + "\n")
    return ws, kept, rep.to_dict()


def _sanitize(ev: dict) -> dict | None:
    keep = SAFE_FIELDS.get(ev.get("type"))
    if keep is None:
        return None
    return {"type": ev["type"], "timestamp": ev.get("timestamp"), **{k: ev[k] for k in keep if k in ev}}


def _normalize(rows, ids: set[str]) -> dict[str, dict]:
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("id") not in ids:
            continue
        cat, act = str(r.get("category", "")).strip(), str(r.get("action", "")).strip()
        rel = str(r.get("relevance", "low")).strip()
        if cat not in CATEGORIES or act not in ACTIONS:
            continue
        rel_i = float(RELEVANCE.index(rel)) if rel in RELEVANCE else 1.0
        out[r["id"]] = {"id": r["id"], "system": "intern_agent", "category": cat, "action": act, "relevance": rel_i,
                        "priority": priority(float(cat == "urgent_action_needed"), float(cat == "important"), rel_i)}
    return out


def summarize_events(events: list[dict], wall_s: float, n_emails: int, simulated: bool) -> dict:
    types = Counter(e["type"] for e in events)
    dec = [e for e in events if e["type"] == "decision"]
    end = next((e for e in reversed(events) if e["type"] == "run_end"), {})
    usage = end.get("usage") or {}
    s1 = end.get("system1") or {}
    s2_cost = float(usage.get("cost_usd") or 0.0)
    if not s2_cost and usage:  # provider did not report cost: estimate from tokens
        p = config.DEEPINFRA_PRICE_PER_MTOK
        s2_cost = (usage.get("input_tokens", 0) * p["input"] + usage.get("output_tokens", 0) * p["output"]) / 1e6
    s1_cost = float(s1.get("cost_usd") or sum(e.get("cost_usd", 0) for e in dec))
    durs = sorted(e["duration_ms"] for e in dec if e.get("ok") and not e.get("cached"))
    return {
        "simulated": simulated, "reason": end.get("reason"), "turns": end.get("turns", types.get("turn_start", 0)),
        "wall_s": wall_s, "n_emails": n_emails,
        "system1_asks": len(dec), "system1_by_purpose": dict(Counter(e.get("purpose") for e in dec)),
        "system1_p50_ms": durs[len(durs) // 2] if durs else None, "system1_cost_usd": s1_cost,
        "nudges": types.get("nudge", 0), "guard_blocks": types.get("tool_guard", 0),
        "tool_calls": types.get("tool_call_start", 0), "compactions": types.get("compaction", 0),
        "system2_input_tokens": usage.get("input_tokens", 0), "system2_output_tokens": usage.get("output_tokens", 0),
        "system2_cost_usd": s2_cost, "total_cost_usd": s1_cost + s2_cost,
        "system2": config.INTERN_SYSTEM2 if not simulated else "mock-qwen (simulated)",
        "system1": config.DREX_MODEL if not simulated else "mock-drex (simulated)",
    }


def _run_intern(ws: Path, n: int, on_event=None, timeout_s: int = 1800) -> tuple[list[dict], float]:
    config.require_real_keys("NACE_API_KEY", "DEEPINFRA_API_KEY")
    exe = shutil.which(config.INTERN_BIN) or str(Path(sys.executable).parent / "intern")
    task = TASK.format(n=n, categories=", ".join(CATEGORIES), relevance=", ".join(RELEVANCE),
                       actions=", ".join(ACTIONS))
    cmd = [exe, "-p", "--json", "--system2", config.INTERN_SYSTEM2, "--system1", config.DREX_MODEL,
           "--max-turns", str(config.INTERN_MAX_TURNS), "--cwd", str(ws), "--no-session", "--no-memory", "--", task]
    env = {**os.environ, "NACE_API_KEY": config.NACE_API_KEY, "DEEPINFRA_API_KEY": config.DEEPINFRA_API_KEY}
    events: list[dict] = []
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=ws, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:
            try:
                ev = _sanitize(json.loads(line))
            except json.JSONDecodeError:
                continue
            if ev:
                events.append(ev)
                if on_event:
                    on_event(ev)
            if time.perf_counter() - t0 > timeout_s:
                proc.terminate()
                break
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode not in (0, None) and not any(e["type"] == "run_end" for e in events):
        err = (proc.stderr.read() or "")[-400:]
        raise RuntimeError(f"intern exited {proc.returncode}: {err}")
    return events, time.perf_counter() - t0


def _simulate(ws: Path, kept: list[dict], profile_text: str, deepinfra, on_event=None) -> tuple[list[dict], float]:
    """Mock-mode stand-in for an Intern session: mock Qwen labels each email; events mimic Intern's stream."""
    events, t, rows = [], 0.0, []
    chunks = [kept[i:i + 15] for i in range(0, len(kept), 15)]

    def emit(ev):
        events.append(ev)
        if on_event:
            on_event(ev)

    emit({"type": "run_start", "model": "mock-qwen (simulated)", "tools": ["read", "write", "edit", "bash"]})
    turn = 0
    for chunk in chunks:
        for step in ("read", "write"):
            turn += 1
            h = int(hashlib.sha1(f"{turn}:{len(kept)}".encode()).hexdigest()[:6], 16)
            emit({"type": "turn_start", "turn": turn})
            emit({"type": "tool_call_start", "turn": turn, "name": step})
            if step == "write":
                for e in chunk:
                    r = triage_email_claude(profile_text, e, deepinfra)
                    rows.append({"id": e["id"], "category": r["category"], "action": r["action"],
                                 "relevance": RELEVANCE[int(r["relevance"])]})
                emit({"type": "decision", "purpose": "guard", "ok": True, "cached": False, "turn": turn,
                      "duration_ms": 380 + h % 90, "cost_usd": 0.0, "input_tokens": 1800 + h % 400,
                      "answers": {"asked": {"noul": 0.93}, "risk": {"noul": 0.04}}})
            t += 4.0 + (h % 1000) / 250  # simulated System 2 turn time
            emit({"type": "tool_call_end", "turn": turn, "name": step, "status": "ok", "duration_ms": 40 + h % 60})
            emit({"type": "decision", "purpose": "supervise", "ok": True, "cached": False, "turn": turn,
                  "duration_ms": 520 + h % 160, "cost_usd": 0.0, "input_tokens": 2500 + h % 900,
                  "answers": {"progress": {"choice": "advancing",
                                           "probabilities": {"advancing": 0.86, "retrying": 0.09, "rereading": 0.05}}}})
            t += 0.6
    if len(chunks) > 3:  # one re-read loop + nudge, as Intern does on small models
        emit({"type": "nudge", "turn": turn, "reason": "stuck"})
    (ws / "agent_triage.json").write_text(json.dumps(rows))
    n_in = sum(len(e.get("body_text", "")[:AGENT_BODY_CHARS]) // 4 + 120 for e in kept) * 3
    emit({"type": "run_end", "reason": "completed", "turns": turn + 1, "duration_ms": int(t * 1000),
          "usage": {"input_tokens": n_in, "output_tokens": 40 * len(kept), "cost_usd": 0.0},
          "system1": {"asks": sum(e["type"] == "decision" for e in events), "cost_usd": 0.0}})
    return events, t


def run_agent(profile_name: str, clients, emails: list[dict] | None = None, limit: int | None = None,
              on_event=None) -> dict:
    from src.loader import load_emails

    emails = emails if emails is not None else load_emails()
    ws, kept, guard = prepare_workspace(profile_name, emails, clients.drex, limit=limit)
    simulated = clients.mock
    if simulated:
        events, wall = _simulate(ws, kept, load_profile(profile_name)["profile_text"], clients.deepinfra, on_event)
    else:
        events, wall = _run_intern(ws, len(kept), on_event)
    out_file = ws / "agent_triage.json"
    try:
        raw = json.loads(out_file.read_text()) if out_file.exists() else []
    except json.JSONDecodeError:
        raw = []
    rows = _normalize(raw, {e["id"] for e in kept})
    summ = summarize_events(events, wall, len(kept), simulated)
    summ.update({"profile": profile_name, "guard": guard, "labeled_by_agent": len(rows),
                 "coverage": len(rows) / len(kept) if kept else 0.0})
    n = max(1, len(kept))
    for r in rows.values():  # per-email share of the session, for the eval table
        r["latency_s"] = wall / n
        r["cost_usd"] = summ["total_cost_usd"] / n
    config.RESULTS_DIR.mkdir(exist_ok=True)
    AGENT_OUT.write_text(json.dumps({"summary": summ, "rows": list(rows.values())}, indent=1))
    with open(EVENTS_OUT, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return {"summary": summ, "rows": rows, "events": events}


def load_agent_result() -> dict | None:
    if not AGENT_OUT.exists():
        return None
    d = json.loads(AGENT_OUT.read_text())
    return {"summary": d["summary"], "rows": {r["id"]: r for r in d["rows"]}}

