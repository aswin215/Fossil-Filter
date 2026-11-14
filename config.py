"""Central config: env loading, mock flag, thresholds, prices, guardrail lists."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
ATT_DIR = RAW_DIR / "att"
EMAILS_PATH = DATA_DIR / "emails.jsonl"
LABELS_PATH = DATA_DIR / "labels.csv"
JOB_GOLD_PATH = DATA_DIR / "job_gold.csv"
SENSITIVE_BENCH_PATH = DATA_DIR / "synthetic_sensitive.jsonl"
PROFILES_DIR = ROOT / "profiles"
RESULTS_DIR = ROOT / "results"
CACHE_DIR = RESULTS_DIR / "cache"
AUDIT_LOG_PATH = RESULTS_DIR / "audit_log.jsonl"


def _env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name, default)
    if val is None:
        return None
    val = val.split("#", 1)[0].strip() if " #" in val else val.strip()
    if val.startswith("<<") or val == "":
        return None  # unfilled placeholder
    return val


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in {"1", "true", "yes", "on"}


USE_MOCKS: bool = _bool("USE_MOCKS", True)

NACE_API_KEY = _env("NACE_API_KEY")
NACE_BASE_URL = (_env("NACE_BASE_URL") or "https://console.nace.ai").rstrip("/")
DREX_MODEL = _env("DREX_MODEL") or "drex-max-v1.1"
NDI_API_KEY = _env("NDI_API_KEY") or NACE_API_KEY
NDI_BASE_URL = (_env("NDI_BASE_URL") or NACE_BASE_URL).rstrip("/")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _env("CLAUDE_MODEL")

SENSITIVE_THRESHOLD = float(_env("SENSITIVE_THRESHOLD") or 0.20)
ESCALATE_THRESHOLD = float(_env("ESCALATE_THRESHOLD") or 0.70)

BODY_MAX_CHARS = 6000
HTTP_TIMEOUT_S = 30.0
TOP_K_SUMMARIES = 5
TOP_N_JOBS = 15

# ---------------------------------------------------------------- prices (USD)
# PLACEHOLDERS: update from the vendors' pricing pages before reporting.
# Claude prices per 1M tokens (from Anthropic's published table as of 2026-11).
CLAUDE_PRICES_PER_MTOK = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-fable-5-2": {"input": 8.00, "output": 40.00},
    "mock-claude": {"input": 4.00, "output": 20.00},  # mock priced like the default model
}
# Drex bills input tokens only (docs). The per-token number did NOT render in the docs:
# PLACEHOLDER - replace with the price shown at console.nace.ai/docs/drex/reference/pricing.
DREX_PRICE_PER_MTOK_INPUT = float(_env("DREX_PRICE_PER_MTOK_INPUT") or 0.20)
DREX_PRICE_IS_PLACEHOLDER = _env("DREX_PRICE_PER_MTOK_INPUT") is None
# NDI per 1,000 page-equivalents (verified from docs, "low" effort).
NDI_EXTRACT_PER_1K_PAGES = 12.0
NDI_PARSE_PER_1K_PAGES = 3.5


def claude_price(model: str | None) -> dict:
    return CLAUDE_PRICES_PER_MTOK.get(model or "", CLAUDE_PRICES_PER_MTOK["mock-claude"])


# ---------------------------------------------------------------- guardrail lists
# Fill in with your own banks / card issuers / payment apps.
SENSITIVE_SENDER_DOMAINS = [
    "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com", "capitalone.com",
    "americanexpress.com", "discover.com", "usbank.com", "schwab.com", "fidelity.com",
    "paypal.com", "venmo.com", "zellepay.com", "cash.app", "irs.gov", "ssa.gov",
]
SENSITIVE_SUBJECT_WORDS = [
    "statement", "verification code", "one-time", "one time passcode", "password reset",
    "reset your password", "balance", "routing number", "security code", "2fa", "login code",
]
SENSITIVE_ATTACHMENT_WORDS = ["statement", "1099", "w-2", "w2", "tax", "paystub", "ssn"]


def require_real_keys(*names: str) -> None:
    """Fail fast with a clear message when running with USE_MOCKS=false and keys are missing."""
    missing = [n for n in names if globals().get(n) is None]
    if missing:
        raise RuntimeError(
            f"USE_MOCKS=false but these settings are missing or still placeholders in .env: "
            f"{', '.join(missing)}. Fill them in or set USE_MOCKS=true."
        )
