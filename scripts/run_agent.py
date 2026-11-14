"""Level B: run an Intern agent session (System 2 = DeepInfra Qwen, System 1 = Drex) over the inbox.

  python scripts/run_agent.py                 # USE_MOCKS from .env (mock = simulated session)
  python scripts/run_agent.py --real          # launches `intern -p --json ...` for real
  python scripts/run_agent.py --limit 30      # first 30 guardrail-passed emails only

Writes results/agent_triage.json (our schema + a run summary) and results/agent_events.jsonl (sanitized
Intern events: System 1 decisions, nudges, guard blocks, tool calls, usage - no email content).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--profile", default="student_jobhunter")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    from src.agent import run_agent
    from src.clients import get_clients
    from src.evaluate import read_labels, triage_metrics

    clients = get_clients(False if a.real else None, need_deepinfra=True)

    def show(ev):
        t = ev["type"]
        if t == "decision":
            print(f"  [system1] {ev.get('purpose')} {ev.get('duration_ms')}ms ok={ev.get('ok')}")
        elif t in ("nudge", "tool_guard"):
            print(f"  [{t}] turn {ev.get('turn')} {ev.get('reason') or ev.get('rule')}")
        elif t == "tool_call_start":
            print(f"  [tool] turn {ev.get('turn')} {ev.get('name')}")
        elif t == "run_end":
            print(f"  [run_end] {ev.get('reason')} after {ev.get('turns')} turns")

    out = run_agent(a.profile, clients, limit=a.limit, on_event=show)
    s = out["summary"]
    m = triage_metrics(out["rows"], read_labels())
    print(f"[agent] {'SIMULATED (mock) ' if s['simulated'] else ''}{s['labeled_by_agent']}/{s['n_emails']} labeled, "
          f"{s['turns']} turns, {s['system1_asks']} System 1 asks, {s['nudges']} nudges, {s['guard_blocks']} guard "
          f"blocks, wall {s['wall_s']:.1f}s, cost ${s['total_cost_usd']:.4f}")
    if m:
        print(f"[agent] category acc {m['category_acc']:.3f}, action acc {m['action_acc']:.3f} on {m['n']} labeled")
    print("[agent] wrote results/agent_triage.json, results/agent_events.jsonl")


if __name__ == "__main__":
    main()
