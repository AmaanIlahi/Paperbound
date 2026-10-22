"""Shared test setup for the public-upload app: sessions and the "uploads are on" switch."""

from paperbound import config, sessions


def new_visitor() -> tuple[str, str]:
    """(cookie value, session hash) of a visitor that is not logged in anywhere yet."""
    return sessions.new_cookie()


def log_in(client, cookie: str) -> None:
    client.cookies.set(sessions.COOKIE_NAME, cookie)


def enable_uploads(monkeypatch) -> None:
    """Uploads need a database URL and an OpenAI key; the tests' own sessions and fakes stand in for both."""
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite://")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
