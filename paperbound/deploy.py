"""Deployment mode and limits, read from the environment on every call (so tests and `fly secrets set` take effect).

Live uploads are off unless LIVE_UPLOADS_ENABLED is "true". When on, uploads need ACCESS_CODE and are limited by size,
page count, uploads per IP per hour and a daily LLM budget. Uploaded documents expire after UPLOAD_TTL_HOURS.
"""

import hmac
import os


def live_enabled() -> bool:
    return os.environ.get("LIVE_UPLOADS_ENABLED", "").strip().lower() == "true"


def access_code() -> str:
    return os.environ.get("ACCESS_CODE", "")


def check_access_code(provided: str | None) -> bool:
    expected = access_code()
    return bool(expected) and provided is not None and hmac.compare_digest(provided.encode(), expected.encode())


def _number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def max_upload_bytes() -> int:
    return int(_number("MAX_UPLOAD_MB", 10) * 1024 * 1024)


def max_pages() -> int:
    return int(_number("MAX_PAGES", 20))


def uploads_per_ip_per_hour() -> int:
    return int(_number("UPLOADS_PER_IP_PER_HOUR", 5))


def daily_llm_budget_usd() -> float:
    return _number("DAILY_LLM_BUDGET_USD", 1.00)


def upload_ttl_hours() -> float:
    return _number("UPLOAD_TTL_HOURS", 24)


def public_settings() -> dict:
    """What the UI may know. Never includes the access code itself."""
    live = live_enabled()
    return {
        "live_uploads_enabled": live,
        "access_code_required": live,
        "max_upload_mb": max_upload_bytes() / (1024 * 1024),
        "max_pages": max_pages(),
        "uploads_per_ip_per_hour": uploads_per_ip_per_hour(),
        "upload_ttl_hours": upload_ttl_hours(),
    }
