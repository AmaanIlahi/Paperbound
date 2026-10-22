"""Limits and availability of public uploads, read from the environment on every call (so tests and `fly secrets set`
take effect).

There is one app, not two modes: uploads are on whenever a database and an OpenAI key are configured, and samples
always work. Uploads are limited by size, page count, uploads per IP per hour and a daily LLM budget, and uploaded
documents expire after UPLOAD_TTL_HOURS.
"""

import os

from paperbound import config

NOTE = "Files are deleted after 24 hours. Don't upload sensitive documents."
DB_DOWN = "Uploads are temporarily unavailable, try a sample"


def _number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def unavailable_reason() -> str | None:
    """Why uploads are off, or None when they are on. Samples work either way."""
    if not config.DATABASE_URL:
        return "No database is configured on this deployment."
    if not config.OPENAI_API_KEY:
        return "No OpenAI key is configured on this deployment."
    return None


def uploads_enabled() -> bool:
    return unavailable_reason() is None


def max_upload_bytes() -> int:
    return int(_number("MAX_UPLOAD_MB", 10) * 1024 * 1024)


def max_pages() -> int:
    return int(_number("MAX_PAGES", 20))


def uploads_per_ip_per_hour() -> int:
    return int(_number("UPLOADS_PER_IP_PER_HOUR", 5))


def daily_llm_budget_usd() -> float:
    return _number("DAILY_LLM_BUDGET_USD", 1.00)


def run_cost_estimate_usd() -> float:
    """What a queued or running job is assumed to cost until it finishes and records its real cost."""
    return _number("RUN_COST_ESTIMATE_USD", 0.02)


def cleanup_interval_seconds() -> float:
    """How often the background task deletes expired uploads. A missing, malformed or non-positive value means 60 minutes."""
    try:
        minutes = _number("CLEANUP_INTERVAL_MINUTES", 60)
    except ValueError:
        return 60 * 60
    return (minutes if minutes > 0 else 60) * 60


def upload_ttl_hours() -> float:
    return _number("UPLOAD_TTL_HOURS", 24)


def public_settings(budget_exhausted: bool = False, database_down: bool = False) -> dict:
    """What the UI may know. `budget_exhausted` comes from the usage ledger and `database_down` from a probe of the
    database; only the caller can do either. A database that is configured but unreachable turns uploads off."""
    reason = unavailable_reason()
    temporarily = reason is None and database_down
    if temporarily:
        reason = DB_DOWN
    return {
        "uploads_enabled": reason is None,
        "unavailable_reason": reason,
        "temporarily_unavailable": temporarily,
        "budget_exhausted": reason is None and budget_exhausted,
        "max_upload_mb": max_upload_bytes() / (1024 * 1024),
        "max_pages": max_pages(),
        "uploads_per_ip_per_hour": uploads_per_ip_per_hour(),
        "upload_ttl_hours": upload_ttl_hours(),
        "note": NOTE,
    }
