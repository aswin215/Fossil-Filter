"""Onboarding: probing questions -> compact profile text (<300 words) stored in profiles/<name>.json.

  python -m src.profile --list
  python -m src.profile --name me            # interactive typed form, Claude writes the profile
  python -m src.profile --from-json answers.json --name me
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

QUESTIONS = [
    ("role", "What is your current role or situation? (e.g. CS grad student, startup founder, parent of two)"),
    ("goals", "What are your top goals for the next 3-6 months?"),
    ("urgent", "What kinds of email are URGENT for you (must act within 24h)?"),
    ("important", "What is important but not urgent?"),
    ("ignore", "What should be ignored or archived without you seeing it?"),
    ("job_targets", "If job hunting: target roles, seniority, locations, tech stack (or 'n/a')"),
    ("dealbreakers", "Job dealbreakers? (e.g. need visa sponsorship, no clearance roles, max years of experience)"),
    ("people", "People or organizations whose emails always matter (advisor, investors, school, ...)"),
]

SYSTEM = ("You write compact user profiles for an email triage model. Given onboarding answers, write a profile in "
          "plain text, under 250 words, second person removed, with short labeled lines: Role, Goals, Urgent, "
          "Important, Ignore, Job targets, Dealbreakers, Key senders. Keep the user's own concrete terms. "
          "No preamble.")


def list_profiles() -> list[str]:
    return sorted(p.stem for p in config.PROFILES_DIR.glob("*.json"))


def load_profile(name: str) -> dict:
    return json.loads((config.PROFILES_DIR / f"{name}.json").read_text())


def build_profile(answers: dict, claude) -> str:
    user = "\n".join(f"{k}: {answers.get(k, '')}" for k, _ in QUESTIONS)
    text = claude.write(SYSTEM, user, max_tokens=1024).data
    words = text.split()
    return " ".join(words[:300]) if len(words) > 300 else text


def save_profile(name: str, answers: dict, text: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("profile name must be alphanumeric/underscore/dash")
    p = config.PROFILES_DIR / f"{name}.json"
    p.write_text(json.dumps({"name": name, "answers": answers, "profile_text": text}, indent=2))
    return p


def main() -> None:
    from src.clients import get_clients

    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--from-json", type=Path)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.name:
        print("profiles:", ", ".join(list_profiles()))
        return
    if a.from_json:
        answers = json.loads(a.from_json.read_text())
    else:
        answers = {k: input(f"{q}\n> ").strip() for k, q in QUESTIONS}
    text = build_profile(answers, get_clients().claude)
    print(f"saved {save_profile(a.name, answers, text)}\n\n{text}")


if __name__ == "__main__":
    main()
