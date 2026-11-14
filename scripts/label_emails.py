"""Interactive CLI to label emails (data/labels.csv) and jobs (data/job_gold.csv).

  python scripts/label_emails.py            # label email category + action
  python scripts/label_emails.py --jobs     # label fit of each job extracted from LinkedIn alerts
  python scripts/label_emails.py --relabel  # revisit already-labeled emails

Shows only sender, subject and the first lines of the body in your terminal; nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import csv
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from src.loader import load_emails  # noqa: E402
from src.triage import ACTIONS, CATEGORIES  # noqa: E402


def pick(prompt: str, options: list[str]) -> str | None:
    for i, o in enumerate(options, 1):
        print(f"   {i}) {o}")
    while True:
        a = input(f"{prompt} [1-{len(options)}, s=skip, q=quit]: ").strip().lower()
        if a == "q":
            raise KeyboardInterrupt
        if a == "s":
            return None
        if a.isdigit() and 1 <= int(a) <= len(options):
            return options[int(a) - 1]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def label_emails(relabel: bool) -> None:
    rows = {r["id"]: r for r in read_csv(config.LABELS_PATH)}
    emails = load_emails()
    todo = [e for e in emails if relabel or e["id"] not in rows]
    print(f"{len(rows)} labeled, {len(todo)} to go. Labels are saved after each email.")
    try:
        for n, e in enumerate(todo, 1):
            print("\n" + "=" * 80 + f"\n[{n}/{len(todo)}] {e['from']}\nSubject: {e['subject']}\n")
            print(textwrap.indent(textwrap.shorten(e["body_text"], 600, placeholder=" ..."), "  "))
            if e["id"] in rows:
                print(f"  (current: {rows[e['id']]['category']} / {rows[e['id']]['action']})")
            cat = pick("category", CATEGORIES)
            if cat is None:
                continue
            act = pick("action", ACTIONS)
            if act is None:
                continue
            rows[e["id"]] = {"id": e["id"], "category": cat, "action": act}
            write_csv(config.LABELS_PATH, list(rows.values()), ["id", "category", "action"])
    except (KeyboardInterrupt, EOFError):
        print("\nstopped; progress saved.")


def label_jobs() -> None:
    from src.clients import get_clients
    from src.jobs import extract_jobs

    gold = read_csv(config.JOB_GOLD_PATH)
    have = {(g["email_id"], g["job_title"], g["company"]) for g in gold}
    ndi = get_clients().ndi
    try:
        for e in [e for e in load_emails() if e["is_linkedin_alert"]]:
            for j in extract_jobs(e, ndi)[0]:
                key = (e["id"], j["title"], j["company"])
                if key in have:
                    continue
                print(f"\n{j['title']} @ {j['company']} ({j.get('location')}) {j.get('seniority')}\n"
                      f"  stack: {', '.join(j.get('tech_stack') or [])}  notes: {j.get('sponsorship_or_clearance_notes')}")
                fit = pick("your fit", ["high", "medium", "low"])
                if fit is None:
                    continue
                gold.append({"email_id": e["id"], "job_title": j["title"], "company": j["company"],
                             "my_rank_or_fit": fit})
                write_csv(config.JOB_GOLD_PATH, gold, ["email_id", "job_title", "company", "my_rank_or_fit"])
    except (KeyboardInterrupt, EOFError):
        print("\nstopped; progress saved.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", action="store_true")
    ap.add_argument("--relabel", action="store_true")
    a = ap.parse_args()
    label_jobs() if a.jobs else label_emails(a.relabel)
