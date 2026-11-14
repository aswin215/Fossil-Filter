"""mbox -> normalized email dicts (data/emails.jsonl). `load_emails()` is the single source interface."""
from __future__ import annotations

import hashlib
import json
import mailbox
import re
import sys
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

BODY_STORE_MAX = 50_000
JOB_ALERT_RE = re.compile(r"\bjobs?\b|hiring|job alert|new jobs|jobs for you", re.I)


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "section", "article"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
    except Exception:  # malformed html: crude fallback
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(p.out)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _decode_header(v: str | None) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return v


def _payload_text(part: Message) -> str:
    raw = part.get_payload(decode=True) or b""
    for cs in (part.get_content_charset(), "utf-8", "cp1252", "latin-1"):
        if not cs:
            continue
        try:
            return raw.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def message_id_to_id(mid: str | None, fallback: str = "") -> str:
    return hashlib.sha1((mid or fallback).strip().encode()).hexdigest()[:12]


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)[:80]


def parse_message(msg: Message, save_attachments: bool = True) -> dict:
    subject = _decode_header(msg.get("Subject"))
    frm = _decode_header(msg.get("From"))
    eid = message_id_to_id(msg.get("Message-ID"), fallback=f"{frm}|{subject}|{msg.get('Date')}")
    try:
        date = parsedate_to_datetime(msg.get("Date")).isoformat()
    except Exception:
        date = ""
    plain, html, atts = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        fname = part.get_filename()
        disp = (part.get("Content-Disposition") or "").lower()
        if fname or "attachment" in disp:
            fname = _decode_header(fname) or "attachment"
            path = config.ATT_DIR / f"{eid}_{_safe_name(fname)}"
            if save_attachments:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(part.get_payload(decode=True) or b"")
            atts.append({"filename": fname, "content_type": ctype,
                         "path": str(path.relative_to(config.ROOT))})
        elif ctype == "text/plain":
            plain.append(_payload_text(part))
        elif ctype == "text/html":
            html.append(_payload_text(part))
    body_html = "\n".join(html)
    body_text = "\n".join(plain).strip() or html_to_text(body_html)
    body_text = re.sub(r"\r\n?", "\n", body_text)[:BODY_STORE_MAX]
    is_alert = "linkedin.com" in frm.lower() and bool(
        JOB_ALERT_RE.search(subject) or "job alert" in body_text.lower()[:2000])
    return {
        "id": eid, "from": frm, "to": _decode_header(msg.get("To")), "date": date, "subject": subject,
        "body_text": body_text, "body_html": body_html[:200_000] if is_alert else "",
        "attachments": atts, "is_linkedin_alert": is_alert,
    }


def mbox_to_jsonl(mbox_paths: list[Path] | None = None, out: Path = config.EMAILS_PATH) -> int:
    paths = mbox_paths or config.MBOX_PATHS
    if not paths:
        raise FileNotFoundError(f"No .mbox files in {config.RAW_DIR}. Export Gmail via Takeout or run "
                                f"`python scripts/make_sample_data.py`.")
    n, seen = 0, set()
    with open(out, "w") as f:
        for p in paths:
            for msg in mailbox.mbox(str(p)):
                e = parse_message(msg)
                if e["id"] in seen:
                    continue
                seen.add(e["id"])
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
                n += 1
    return n


def load_emails(path: Path = config.EMAILS_PATH, limit: int | None = None) -> list[dict]:
    """Single interface for email sources. Swap this for a live Gmail/IMAP reader later (read-only)."""
    if not path.exists():
        mbox_to_jsonl()
    out = []
    with open(path) as f:
        for line in f:
            out.append(json.loads(line))
            if limit and len(out) >= limit:
                break
    return out


def model_body(email: dict) -> str:
    return email.get("body_text", "")[: config.BODY_MAX_CHARS]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Parse mbox files in data/raw into data/emails.jsonl")
    ap.add_argument("mbox", nargs="*", type=Path)
    a = ap.parse_args()
    count = mbox_to_jsonl(a.mbox or None)
    emails = load_emails()
    print(f"wrote {count} emails -> {config.EMAILS_PATH.relative_to(config.ROOT)}; "
          f"{sum(e['is_linkedin_alert'] for e in emails)} LinkedIn alerts, "
          f"{sum(bool(e['attachments']) for e in emails)} with attachments")
