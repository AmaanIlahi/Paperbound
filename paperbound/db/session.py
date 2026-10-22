from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from paperbound import config

_engine = None
_sessionmaker = None


def get_engine():
    """Created on first use, so the app can start (and serve the demo) with no DATABASE_URL at all."""
    global _engine, _sessionmaker
    if _engine is None:
        options = {"connect_args": {"connect_timeout": 5}} if config.DATABASE_URL.startswith("postgres") else {}
        _engine = create_engine(config.DATABASE_URL, pool_size=5, max_overflow=0, pool_pre_ping=True, **options)
        _sessionmaker = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session():
    if not config.DATABASE_URL:
        raise HTTPException(503, "No database is configured on this deployment.")
    get_engine()
    with _sessionmaker() as session:
        yield session


def new_session():
    """A session outside a request (background jobs)."""
    get_engine()
    return _sessionmaker()


def session_factory():
    """Dependency: a callable that opens sessions on demand, for streams that outlive a request's own session."""
    return new_session
