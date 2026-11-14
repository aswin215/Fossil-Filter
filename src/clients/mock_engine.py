"""Deterministic heuristic 'model' used by all mock clients.

Scores labels with keyword lexicons + hash-seeded noise, so the pipeline, UI and eval code can be
built before real keys exist. Noise level differs per mock (Drex vs Claude) so comparisons are not
trivially identical. Mock numbers are NOT evidence about the real models.
"""
from __future__ import annotations

import hashlib
import math
import re

LEXICON: dict[str, list[str]] = {
    # triage category
    "urgent_action_needed": ["urgent", "asap", "today", "by eod", "tonight", "deadline", "action required",
                             "within 24 hours", "final notice", "immediately", "expires", "confirm by", "due tomorrow",
                             "respond by", "last day"],
    "important": ["interview", "application", "offer", "advisor", "professor", "meeting", "feedback", "update on",
                  "next steps", "your team", "project", "investor", "school", "teacher", "doctor", "appointment"],
    "informational": ["newsletter", "digest", "weekly", "announcement", "event", "webinar", "release notes",
                      "notification", "community", "recap", "fyi", "reminder"],
    "job_alert": ["job alert", "jobs for you", "new jobs", "are hiring", "recommended jobs", "apply now",
                  "linkedin", "handshake", "positions"],
    "promotion_or_spam": ["% off", "sale", "deal", "unsubscribe", "limited time", "free shipping", "coupon",
                          "winner", "congratulations you", "promo", "exclusive offer", "shop now"],
    # actions
    "reply_today": ["please reply", "let me know", "can you", "could you", "confirm", "respond", "rsvp by"],
    "read_later": ["newsletter", "digest", "article", "read", "recap", "update"],
    "track_as_application": ["application", "applied", "candidate", "interview", "assessment", "recruit"],
    "add_to_calendar": ["meeting", "calendar", "invite", "zoom", "pm on", "am on", "schedule", "event on"],
    "archive": ["receipt", "notification", "shipped", "your order", "fyi"],
    "ignore": ["unsubscribe", "% off", "sale", "promo", "winner"],
    # guardrail
    "otp_or_code": ["verification code", "one-time", "passcode", "otp", "login code", "security code",
                    "your code is", "2fa"],
    "bank_or_card_statement": ["statement", "balance", "account ending", "payment due", "minimum payment",
                               "transactions", "available credit"],
    "credentials_or_reset_link": ["reset your password", "password reset", "sign-in link", "login link",
                                  "magic link", "temporary password", "credentials"],
    "tax_or_government_id": ["1099", "w-2", "tax return", "ssn", "social security", "irs", "passport number",
                             "driver's license"],
    "medical": ["lab results", "diagnosis", "prescription", "patient portal", "medical record", "test results"],
    "none": [],
    # jobs (noul)
    "is_target_role": ["intern", "internship", "new grad", "entry level", "entry-level", "graduate", "junior",
                       "university"],
    "has_dealbreaker": ["clearance", "ts/sci", "10+ years", "8+ years", "7+ years", "5+ years", "senior",
                        "staff", "principal", "no sponsorship", "us citizens only", "citizenship required"],
}
PRIOR = {"none": 2.2, "informational": 0.4, "read_later": 0.3}


def _noise(key: str, scale: float) -> float:
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return (h - 0.5) * 2 * scale


def _hits(text: str, words: list[str]) -> int:
    return sum(text.count(w) for w in words)


def _softmax(scores: dict[str, float], temp: float = 1.0) -> dict[str, float]:
    m = max(scores.values())
    ex = {k: math.exp((v - m) / temp) for k, v in scores.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z+#.-]{2,}", s.lower())} - STOP


STOP = {"the", "and", "for", "you", "your", "with", "this", "that", "are", "from", "our", "have", "will",
        "not", "but", "all", "any", "can", "who", "what", "about", "into", "more", "want", "like", "they",
        "body", "subject", "date", "profile", "email", "job", "job:", "from:", "subject:"}


def _item_text(text: str) -> str:
    """Drop the PROFILE block so profile keywords don't leak into every email's score; weight the subject."""
    t = text.split("\n\nEMAIL:\n", 1)[-1].split("\n\nJOB:\n", 1)[-1].lower()
    subj = re.search(r"subject: (.*)", t)
    return t + ("\n" + subj.group(1)) * 2 if subj else t


def choice(text: str, labels: list[str], seed: str, noise: float) -> dict[str, float]:
    t = _item_text(text)
    scores = {}
    for lab in labels:
        words = LEXICON.get(lab) or [w for w in lab.replace("_", " ").split() if len(w) > 3]
        scores[lab] = 1.6 * math.log1p(_hits(t, words)) + PRIOR.get(lab, 0.0) + _noise(seed + lab, noise)
    return _softmax(scores, temp=0.7)


def relevance(state: str, n_levels: int, seed: str, noise: float) -> dict[str, float]:
    """Overlap between the PROFILE block and the rest of the state -> distribution over ordered levels."""
    parts = state.split("\n\n", 1)
    prof = _words(parts[0])
    rest = _words(parts[1] if len(parts) > 1 else "")
    overlap = len(prof & rest) / (len(rest) ** 0.5 + 1)
    target = min(n_levels - 1, overlap * 2.2) + _noise(seed, noise * 1.5)
    target = max(0.0, min(n_levels - 1.0, target))
    scores = {str(i): -((i - target) ** 2) for i in range(n_levels)}
    return _softmax(scores, temp=0.6)


def noul(text: str, qid: str, seed: str, noise: float) -> float:
    t = _item_text(text)
    hits = _hits(t, LEXICON.get(qid, []))
    logit = -1.4 + 1.6 * hits + _noise(seed, noise * 2)
    return 1 / (1 + math.exp(-logit))
