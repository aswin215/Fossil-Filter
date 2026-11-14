"""One command: load emails -> full evaluation (incl. the Intern agent run) -> per-profile inbox runs.

  python scripts/run_all.py            # uses USE_MOCKS from .env
  python scripts/run_all.py --real     # force real APIs
  python scripts/run_all.py --sample   # (re)generate the synthetic sample in data/sample/ first
  python scripts/run_all.py --skip agent personas   # skip the (slow, paid) Intern agent session and personas
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--profile", default="student_jobhunter")
    ap.add_argument("--agent-limit", type=int, default=None, help="cap emails given to the Intern agent session")
    ap.add_argument("--skip", nargs="*", default=[], choices=["jobs", "guard", "personas", "inbox", "agent"])
    a = ap.parse_args()
    t0 = time.time()

    if a.sample or not all(p.exists() for p in config.MBOX_PATHS):
        if not a.sample:
            print("No sample mbox yet - generating the synthetic sample (put a Takeout export in data/raw/ for real data).")
        from scripts.make_sample_data import main as make_sample
        make_sample()
    from src.loader import mbox_to_jsonl
    print(f"[load] {mbox_to_jsonl()} emails -> data/emails.jsonl")

    from src.clients import get_clients
    from src.evaluate import run
    from src.pipeline import run_inbox
    from src.profile import list_profiles

    clients = get_clients(False if a.real else None)
    run(clients, a.profile, do_jobs="jobs" not in a.skip, do_guard="guard" not in a.skip,
        do_personas="personas" not in a.skip, do_agent="agent" not in a.skip,
        agent_limit=a.agent_limit)
    if "inbox" not in a.skip:
        for p in list_profiles():
            out = run_inbox(p, clients)
            print(f"[inbox] {p}: {out['guard']['scanned']} scanned, {out['guard']['withheld']} withheld, "
                  f"{len(out['emails'])} triaged, {len(out['jobs'])} jobs, {out['escalated_frac']:.0%} escalated")
    print(f"[done] {time.time() - t0:.1f}s -> results/summary.md ; now: streamlit run app.py")


if __name__ == "__main__":
    main()
