"""Drex email triage: one forward pass answers category + relevance + action for an email."""
from __future__ import annotations

from src.clients.base import DrexResult, q_choice, q_score
from src.loader import model_body

CATEGORIES = ["urgent_action_needed", "important", "informational", "job_alert", "promotion_or_spam"]
ACTIONS = ["reply_today", "read_later", "track_as_application", "add_to_calendar", "archive", "ignore"]
RELEVANCE = ["none", "low", "medium", "high"]

CATEGORY_DESC = {
    "urgent_action_needed": "The user must act within ~24 hours given their profile (deadline, scheduling, "
                            "expiring opportunity, direct request from a key sender).",
    "important": "Matters to the user's goals or key people, but is not time-critical.",
    "informational": "Newsletters, notifications, digests, receipts, FYI announcements.",
    "job_alert": "Automated job alert or job recommendation digest listing openings.",
    "promotion_or_spam": "Marketing, sales, deals, unsolicited offers, scams.",
}
ACTION_DESC = {
    "reply_today": "The user should write a reply today.",
    "read_later": "Worth reading when the user has time; no reply needed.",
    "track_as_application": "A job application status/update the user should log in their tracker.",
    "add_to_calendar": "Contains a meeting, event or deadline to put on the calendar.",
    "archive": "No action; keep for reference.",
    "ignore": "Not useful to this user.",
}

TRIAGE_QUESTIONS = {
    "category": q_choice("Classify this EMAIL for the user described in PROFILE.", CATEGORY_DESC),
    "relevance": q_score("How relevant is this EMAIL to the goals in the user's PROFILE?",
                         ["none: unrelated to the user's goals", "low: marginally related",
                          "medium: clearly related", "high: directly advances a stated goal or involves a key sender"]),
    "action": q_choice("What should this user do with this EMAIL?", ACTION_DESC),
}


def email_block(email: dict) -> str:
    atts = ", ".join(a["filename"] for a in email.get("attachments", []))
    return (f"From: {email['from']}\nSubject: {email['subject']}\nDate: {email.get('date', '')}\n"
            + (f"Attachments: {atts}\n" if atts else "") + f"Body: {model_body(email)}")


def build_state(profile_text: str, email: dict) -> str:
    return f"PROFILE:\n{profile_text}\n\nEMAIL:\n{email_block(email)}"


def priority(p_urgent: float, p_important: float, relevance_expected: float, n_levels: int = 4) -> float:
    """0..1. Category urgency dominates; expected relevance scales it."""
    rel = relevance_expected / (n_levels - 1)
    return round((p_urgent + 0.5 * p_important) * (0.5 + 0.5 * rel), 4)


def result_from_drex(email_id: str, r: DrexResult) -> dict:
    cat, rel, act = r.answers["category"], r.answers["relevance"], r.answers["action"]
    return {
        "id": email_id, "system": "drex",
        "category": max(cat.probabilities, key=cat.probabilities.get),
        "category_probs": cat.probabilities,
        "action": max(act.probabilities, key=act.probabilities.get),
        "action_probs": act.probabilities,
        "relevance": rel.score,
        "relevance_probs": rel.probabilities,
        "confidence": min(cat.top_prob, act.top_prob),
        "relevance_entropy": rel.entropy_norm,
        "priority": priority(cat.probabilities.get("urgent_action_needed", 0),
                             cat.probabilities.get("important", 0), rel.score),
        "latency_s": r.latency_s, "input_tokens": r.input_tokens, "output_tokens": 0, "model": r.model,
    }


def triage_email(profile_text: str, email: dict, drex) -> dict:
    return result_from_drex(email["id"], drex.evaluate(build_state(profile_text, email), TRIAGE_QUESTIONS))
