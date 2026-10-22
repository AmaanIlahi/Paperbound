"""Anonymous browser sessions: who owns an upload.

A visitor gets a signed, HttpOnly, SameSite=Lax cookie on first contact. It holds a random token and an HMAC of it with
SESSION_SECRET. Only the sha256 of the token (the "session hash") is stored on documents and runs, so a database leak
does not give anyone a working cookie, and a forged or edited cookie is simply treated as a new visitor.

Without SESSION_SECRET the app signs with a random per-process secret: everything still works, but visitors lose
their runs when the process restarts. Set SESSION_SECRET in production.
"""

import hashlib
import hmac
import logging
import os
import secrets

from fastapi import Request, Response

log = logging.getLogger(__name__)

COOKIE_NAME = "pb_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
_fallback_secret = secrets.token_hex(32)
_warned = False


def _secret() -> bytes:
    global _warned
    configured = os.environ.get("SESSION_SECRET", "")
    if configured:
        return configured.encode()
    if not _warned:
        log.warning("SESSION_SECRET is not set: using a random per-process secret, sessions will not survive a restart.")
        _warned = True
    return _fallback_secret.encode()


def _sign(token: str) -> str:
    return hmac.new(_secret(), token.encode(), hashlib.sha256).hexdigest()


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_cookie() -> tuple[str, str]:
    """(cookie value, session hash) for a brand-new visitor."""
    token = secrets.token_urlsafe(32)
    return f"{token}.{_sign(token)}", session_hash(token)


def read_cookie(value: str | None) -> str | None:
    """The session hash for a genuine cookie, else None."""
    if not value or "." not in value:
        return None
    token, _, signature = value.rpartition(".")
    if not token or not hmac.compare_digest(signature.encode(), _sign(token).encode()):
        return None
    return session_hash(token)


def set_cookie(response: Response, request: Request, value: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE_NAME, value, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=secure, path="/")


def current_session(request: Request, response: Response) -> str:
    """FastAPI dependency: the caller's session hash, issuing a cookie on the response if they have none."""
    existing = read_cookie(request.cookies.get(COOKIE_NAME))
    if existing:
        return existing
    value, hashed = new_cookie()
    set_cookie(response, request, value)
    return hashed


def peek_session(request: Request) -> str | None:
    """The session hash if the request carries a genuine cookie; never issues one."""
    return read_cookie(request.cookies.get(COOKIE_NAME))
