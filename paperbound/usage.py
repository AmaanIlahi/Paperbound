"""Public-deployment bookkeeping in Postgres: per-IP upload rate limit, daily LLM budget, and expiry of uploads."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from paperbound.db.models import Document, UsageLedger
from paperbound.storage import Storage

log = logging.getLogger(__name__)


def ip_hash(ip: str) -> str:
    """Only a hash of the client address is stored."""
    return hashlib.sha256(ip.encode()).hexdigest()


def uploads_last_hour(session: Session, hashed_ip: str, now: datetime) -> int:
    return session.scalar(
        select(func.count()).select_from(UsageLedger).where(
            UsageLedger.kind == "upload", UsageLedger.ip_hash == hashed_ip, UsageLedger.created_at > now - timedelta(hours=1)
        )
    ) or 0


def record_upload(session: Session, hashed_ip: str, now: datetime) -> None:
    session.add(UsageLedger(kind="upload", ip_hash=hashed_ip, usd=0.0, created_at=now))
    session.flush()


def day_start(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(session: Session, now: datetime) -> float:
    """LLM spend recorded since midnight UTC."""
    return float(
        session.scalar(
            select(func.coalesce(func.sum(UsageLedger.usd), 0.0)).where(
                UsageLedger.kind == "llm", UsageLedger.created_at >= day_start(now)
            )
        )
        or 0.0
    )


def record_spend(session: Session, usd: float, now: datetime) -> None:
    if usd > 0:
        session.add(UsageLedger(kind="llm", usd=usd, created_at=now))
        session.flush()


def cleanup_expired(session: Session, storage: Storage, now: datetime) -> int:
    """Delete uploads past their expiry: files first, then the document row (runs, fields, actions cascade).

    Only documents with an expires_at are ever touched, so documents saved without live-upload mode are left alone.
    The usage ledger is kept: the budget and rate limit must survive the deletion of what they counted.
    """
    expired = session.execute(
        select(Document.id, Document.sha256).where(Document.expires_at.is_not(None), Document.expires_at < now)
    ).all()
    for doc_id, sha256 in expired:
        try:
            storage.delete_prefix(f"documents/{sha256}")
        except Exception:  # a missing file must not keep the row alive forever
            log.exception("could not delete files for document %s", doc_id)
        session.execute(delete(Document).where(Document.id == doc_id))
    if expired:
        session.commit()
    return len(expired)
