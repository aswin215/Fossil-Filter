"""Layered, fail-closed sensitive-data guardrail. Runs BEFORE any email leaves the machine for triage.

Layer 1  local rules (regex, Luhn, sender/subject/attachment lists)  -> hit = DROP, nothing sent anywhere
Layer 2  Drex semantic check on REDACTED text                        -> sum P(sensitive classes) > threshold = DROP
Layer 3  any exception/timeout in layers 1-2                         -> DROP
Audit log records only email ids, layer and rule names.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field

import config
from src.clients.base import q_choice

SENSITIVE_CLASSES = {
    "otp_or_code": "Contains a one-time passcode, verification or login code.",
    "bank_or_card_statement": "Bank, card or payment statement, balance, or account/routing numbers.",
    "credentials_or_reset_link": "Passwords, credentials, password-reset or magic sign-in links.",
    "tax_or_government_id": "Tax forms (W-2, 1099), SSN, passport, driver's license or other government IDs.",
    "medical": "Medical results, diagnoses, prescriptions or patient-portal health information.",
    "none": "None of the above; ordinary email.",
}
GUARD_QUESTION = {"sensitive_class": q_choice(
    "Does this email contain sensitive personal data of one of these kinds? Digits may be redacted as [NUM].",
    SENSITIVE_CLASSES)}

RX = {
    "otp_code": re.compile(
        r"(?:verification|security|login|sign-?in|one[- ]time|confirmation|2fa|auth\w*)\s*(?:code|passcode|pin)\b"
        r"[^0-9\n]{0,25}\d{4,8}\b|\b(?:otp|passcode)\b[^0-9\n]{0,25}\d{4,8}\b|\byour code is\s*:?\s*\d{4,8}\b", re.I),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "account_or_routing": re.compile(r"\b(?:routing|account|acct)\s*(?:number|no\.?|#)?\s*[:#]?\s*\d{6,17}\b", re.I),
    "password_in_body": re.compile(r"\b(?:temporary )?password\s*(?:is|:|=)\s*\S{4,}", re.I),
    "reset_link": re.compile(r"\b(?:reset|change)\s+(?:your\s+)?password\b.{0,120}https?://", re.I | re.S),
    "tax_form": re.compile(r"\b(?:form\s+)?(?:1099(?:-[A-Z]{2,4})?|W-2)\b", re.I),
}
CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def luhn_ok(digits: str) -> bool:
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        s += d
        alt = not alt
    return s % 10 == 0


def _domain(frm: str) -> str:
    m = re.search(r"@([A-Za-z0-9.-]+)", frm or "")
    return m.group(1).lower() if m else ""


def layer1_rules(email: dict) -> list[str]:
    hits = []
    subj = (email.get("subject") or "").lower()
    body = email.get("body_text") or ""
    text = f"{email.get('subject', '')}\n{body}"
    dom = _domain(email.get("from", ""))
    if any(dom == d or dom.endswith("." + d) for d in config.SENSITIVE_SENDER_DOMAINS):
        hits.append("sender_domain")
    hits += [f"subject:{w}" for w in config.SENSITIVE_SUBJECT_WORDS if w in subj][:1]
    for a in email.get("attachments", []):
        fn = a.get("filename", "").lower()
        if any(w in fn for w in config.SENSITIVE_ATTACHMENT_WORDS):
            hits.append("attachment_name")
            break
    for name, rx in RX.items():
        if rx.search(text):
            hits.append(name)
    for m in CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            hits.append("card_luhn")
            break
    return hits


def redact(text: str) -> str:
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[EMAIL]", text)
    return re.sub(r"\d(?:[\d -]{2,}\d)", "[NUM]", text)


def guard_state(email: dict) -> str:
    return redact(f"From: {email.get('from', '')}\nSubject: {email.get('subject', '')}\n"
                  f"Body: {(email.get('body_text') or '')[: config.BODY_MAX_CHARS]}")


def layer2_drex(email: dict, drex, threshold: float) -> tuple[bool, str | None, dict]:
    r = drex.evaluate(guard_state(email), GUARD_QUESTION)
    probs = r.answers["sensitive_class"].probabilities
    p_sens = sum(v for k, v in probs.items() if k != "none")
    top = max((k for k in probs if k != "none"), key=probs.get)
    return p_sens > threshold, top, {"latency_s": r.latency_s, "input_tokens": r.input_tokens, "p_sensitive": p_sens}


CLAUDE_GUARD_SYSTEM = ("Classify whether this email contains sensitive personal data. Classes:\n" +
                       "\n".join(f"- {k}: {v}" for k, v in SENSITIVE_CLASSES.items()) + "\nAnswer with JSON only.")
CLAUDE_GUARD_SCHEMA = {"type": "object", "properties": {"sensitive_class": {"type": "string",
                                                                              "enum": list(SENSITIVE_CLASSES)}},
                       "required": ["sensitive_class"], "additionalProperties": False}


def layer2_claude(email: dict, claude) -> tuple[bool, str | None, dict]:
    """Same check done by Claude (benchmark comparison only)."""
    r = claude.judge(CLAUDE_GUARD_SYSTEM, guard_state(email), CLAUDE_GUARD_SCHEMA, max_tokens=1024)
    cls = r.data["sensitive_class"]
    return cls != "none", cls, {"latency_s": r.latency_s, "input_tokens": r.input_tokens,
                                "output_tokens": r.output_tokens}


@dataclass
class GuardReport:
    scanned: int = 0
    withheld: int = 0
    rule_counts: Counter = field(default_factory=Counter)
    layer_counts: Counter = field(default_factory=Counter)
    drex_latency_s: list = field(default_factory=list)
    drex_input_tokens: int = 0

    def to_dict(self) -> dict:
        return {"scanned": self.scanned, "withheld": self.withheld, "rule_counts": dict(self.rule_counts),
                "layer_counts": dict(self.layer_counts), "drex_input_tokens": self.drex_input_tokens}


def _audit(eid: str, layer: str, rules: list[str]) -> None:
    config.RESULTS_DIR.mkdir(exist_ok=True)
    with open(config.AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": round(time.time(), 1), "id": eid, "layer": layer, "rules": rules}) + "\n")


def check(email: dict, drex=None, threshold: float | None = None, use_semantic: bool = True) -> tuple[bool, str, list[str], dict]:
    """Returns (keep, layer, rules, meta). Fail closed on any error."""
    t = config.SENSITIVE_THRESHOLD if threshold is None else threshold
    try:
        rules = layer1_rules(email)
        if rules:
            return False, "layer1_rules", rules, {}
        if use_semantic and drex is not None:
            drop, cls, meta = layer2_drex(email, drex, t)
            if drop:
                return False, "layer2_drex", [f"drex:{cls}"], meta
            return True, "pass", [], meta
        return True, "pass", [], {}
    except Exception as e:  # Layer 3: fail closed
        return False, "layer3_error", [f"error:{type(e).__name__}"], {}


def filter_emails(emails: list[dict], drex=None, threshold: float | None = None, use_semantic: bool = True,
                  audit: bool = True) -> tuple[list[dict], GuardReport]:
    rep, kept = GuardReport(), []
    for e in emails:
        rep.scanned += 1
        keep, layer, rules, meta = check(e, drex, threshold, use_semantic)
        if "latency_s" in meta:
            rep.drex_latency_s.append(meta["latency_s"])
            rep.drex_input_tokens += meta.get("input_tokens", 0)
        if keep:
            kept.append(e)
            continue
        rep.withheld += 1
        rep.layer_counts[layer] += 1
        for r in rules:
            rep.rule_counts[r.split(":")[0] if layer == "layer1_rules" and r.startswith("subject:") else r] += 1
        if audit:
            _audit(e.get("id", "?"), layer, rules)
    return kept, rep
