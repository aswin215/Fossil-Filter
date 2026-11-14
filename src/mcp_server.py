"""Level C: Inbox Predator as a small MCP server (stdio) for the Intern agent.

Tools (all scoped to the agent workspace, which only ever holds guardrail-passed emails):
  list_emails(offset, limit)            ids, senders, subjects
  get_email(id)                         one email (truncated body)
  score_email(id)                       Drex's calibrated triage probabilities (Level A path, via intern.decisions)
  save_label(id, category, relevance, action)   records the agent's decision in agent_triage.json

Run by Intern from the config.toml that src/agent.py writes:
  python -m src.mcp_server --workspace data/agent_workspace
Logs nothing about email content (stdout is the MCP channel; nothing else is printed).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from src.triage import ACTIONS, CATEGORIES, RELEVANCE, build_state, result_from_drex, TRIAGE_QUESTIONS  # noqa: E402


def build_server(workspace: Path, drex=None, mock: bool = True) -> FastMCP:
    ws = Path(workspace)
    emails = [json.loads(x) for x in (ws / "emails.jsonl").read_text().splitlines() if x.strip()]
    by_id = {e["id"]: e for e in emails}
    profile = (ws / "profile.txt").read_text()
    out_path = ws / "agent_triage.json"
    lock = threading.Lock()
    labels: dict[str, dict] = {}
    if out_path.exists():
        try:
            labels = {r["id"]: r for r in json.loads(out_path.read_text())}
        except (json.JSONDecodeError, KeyError, TypeError):
            labels = {}
    state = {"drex": drex}

    def _drex():
        if state["drex"] is None:
            from src.clients.drex_client import DrexClient, MockDrexClient

            state["drex"] = MockDrexClient() if mock else DrexClient()
        return state["drex"]

    mcp = FastMCP("inbox")

    @mcp.tool()
    def list_emails(offset: int = 0, limit: int = 20) -> str:
        """List emails in the inbox: id, from, subject, and whether you already saved a label. Page with offset."""
        limit = max(1, min(int(limit), 50))
        rows = [{"id": e["id"], "from": e["from"], "subject": e["subject"], "labeled": e["id"] in labels}
                for e in emails[offset:offset + limit]]
        return json.dumps({"total": len(emails), "labeled": len(labels), "offset": offset, "emails": rows})

    @mcp.tool()
    def get_email(id: str) -> str:  # noqa: A002 - MCP argument name
        """Get one email by id: from, subject, date and body (truncated)."""
        e = by_id.get(id)
        return json.dumps(e) if e else json.dumps({"error": f"unknown id {id}"})

    @mcp.tool()
    def score_email(id: str) -> str:  # noqa: A002
        """Drex's calibrated probabilities for this email's category, relevance and action, for this user."""
        e = by_id.get(id)
        if not e:
            return json.dumps({"error": f"unknown id {id}"})
        email = {**e, "body_text": e.get("body", ""), "attachments": []}
        r = result_from_drex(id, _drex().evaluate(build_state(profile, email), TRIAGE_QUESTIONS, purpose="mcp_score"))
        rnd = lambda d: {k: round(v, 3) for k, v in d.items()}  # noqa: E731
        return json.dumps({"id": id, "category_probs": rnd(r["category_probs"]), "action_probs": rnd(r["action_probs"]),
                           "relevance_expected_level": round(r["relevance"], 2), "relevance_levels": RELEVANCE,
                           "confidence": round(r["confidence"], 3)})

    @mcp.tool()
    def save_label(id: str, category: str, relevance: str, action: str) -> str:  # noqa: A002
        """Record your decision for one email. category, relevance and action must be one of the allowed labels."""
        bad = [f"{k}={v!r}" for k, v, allowed in (("category", category, CATEGORIES), ("relevance", relevance, RELEVANCE),
                                                  ("action", action, ACTIONS)) if v not in allowed]
        if id not in by_id:
            bad.append(f"unknown id {id}")
        if bad:
            return json.dumps({"ok": False, "errors": bad, "allowed": {"category": CATEGORIES, "relevance": RELEVANCE,
                                                                       "action": ACTIONS}})
        with lock:
            labels[id] = {"id": id, "category": category, "relevance": relevance, "action": action}
            out_path.write_text(json.dumps(list(labels.values())))
        return json.dumps({"ok": True, "labeled": len(labels), "total": len(emails)})

    return mcp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--real", action="store_true", help="real Drex (NACE_API_KEY) instead of the mock backend")
    a = ap.parse_args()
    build_server(a.workspace, mock=not a.real).run("stdio")


if __name__ == "__main__":
    main()
