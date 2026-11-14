"""Central config: env loading, mock flag, thresholds, prices, guardrail lists."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
# PUBLIC repo: only synthetic data in data/sample/ is committed. Your private inbox lives in gitignored paths:
#   data/raw/*.mbox (Takeout export), data/labels.csv, data/job_gold.csv (your own labels).
SAMPLE_DIR = DATA_DIR / "sample"
RAW_DIR = DATA_DIR / "raw"
ATT_DIR = RAW_DIR / "att"
EMAILS_PATH = DATA_DIR / "emails.jsonl"  # normalized output of the loader (gitignored)
SENSITIVE_BENCH_PATH = SAMPLE_DIR / "synthetic_sensitive.jsonl"


def _private_mbox() -> list[Path]:
    return sorted(RAW_DIR.glob("*.mbox")) if RAW_DIR.exists() else []


# "private" when a Takeout mbox is in data/raw/, else the committed synthetic sample.
DATA_SOURCE = "private" if _private_mbox() else "sample"
MBOX_PATHS = _private_mbox() if DATA_SOURCE == "private" else [SAMPLE_DIR / "sample.mbox"]
LABELS_PATH = (DATA_DIR if DATA_SOURCE == "private" else SAMPLE_DIR) / "labels.csv"
JOB_GOLD_PATH = (DATA_DIR if DATA_SOURCE == "private" else SAMPLE_DIR) / "job_gold.csv"
AGENT_WORKSPACE = DATA_DIR / "agent_workspace"  # guardrail-passed emails handed to the Intern agent (gitignored)
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
# Drex: POST {NACE_BASE_URL}/systemone. The console base includes /v1 (Intern's NACE_CONSOLE_URL).
NACE_BASE_URL = (_env("NACE_BASE_URL") or "https://console.nace.ai/v1").rstrip("/")
if not NACE_BASE_URL.endswith("/v1"):
    NACE_BASE_URL += "/v1"
# drex-latest (console default, currently = drex-max-v1.1), drex-max-v1.1, or decisions:nace-ai/drex-max (OpenRouter)
DREX_MODEL = _env("DREX_MODEL") or "drex-latest"
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/v1"
NDI_API_KEY = _env("NDI_API_KEY") or NACE_API_KEY
# NDI paths are /v1/documents/...; accept a base with or without /v1.
NDI_BASE_URL = (_env("NDI_BASE_URL") or "https://console.nace.ai").rstrip("/").removesuffix("/v1")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _env("CLAUDE_MODEL")
# DeepInfra (partner tool): System 2 for the Intern agent, and a second LLM baseline.
DEEPINFRA_API_KEY = _env("DEEPINFRA_API_KEY")
DEEPINFRA_BASE_URL = (_env("DEEPINFRA_BASE_URL") or "https://api.deepinfra.com/v1/openai").rstrip("/")
DEEPINFRA_MODEL = _env("DEEPINFRA_MODEL") or "Qwen/Qwen3.8-27B"
INTERN_BIN = _env("INTERN_BIN") or "intern"
INTERN_SYSTEM2 = _env("INTERN_SYSTEM2") or f"deepinfra/{DEEPINFRA_MODEL}"
INTERN_MAX_TURNS = int(_env("INTERN_MAX_TURNS") or 60)
INTERN_USE_MCP: bool = _bool("INTERN_USE_MCP", False)  # Level C: the agent uses our MCP tools instead of raw files

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
# DeepInfra per 1M tokens. PLACEHOLDER: update from deepinfra.com/pricing (the API's usage.estimated_cost
# is used instead when it is returned).
DEEPINFRA_PRICE_PER_MTOK = {"input": float(_env("DEEPINFRA_PRICE_IN") or 0.10),
                            "output": float(_env("DEEPINFRA_PRICE_OUT") or 0.40)}
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
