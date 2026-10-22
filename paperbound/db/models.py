from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# The SQLite variants exist only so unit tests can run on an in-memory database; Postgres is unchanged.
Json = JSON().with_variant(JSONB(), "postgresql")
BigPK = BigInteger().with_variant(Integer(), "sqlite")
StrArray = ARRAY(String).with_variant(JSON(), "sqlite")
IntArray = ARRAY(BigInteger).with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    filename: Mapped[str] = mapped_column(String)
    storage_key: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    doc_type: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set only for uploads made while live uploads are enabled; the cleanup job deletes only rows that have one.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # sha256 of the uploader's session token. Documents and runs from before sessions existed have none and are
    # visible to nobody.
    session_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    pages: Mapped[list["Page"]] = relationship(back_populates="document", order_by="Page.page_no")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    route: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text, default="")
    words_json: Mapped[list] = mapped_column(Json, default=list)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    image_key: Mapped[str | None] = mapped_column(String)
    width_pt: Mapped[float] = mapped_column(Float)
    height_pt: Mapped[float] = mapped_column(Float)

    document: Mapped[Document] = relationship(back_populates="pages")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    session_hash: Mapped[str | None] = mapped_column(String(64), index=True)


class Job(Base):
    """Work queue for runs: the in-process worker claims one queued row at a time with FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued | running | done | failed
    payload: Mapped[dict] = mapped_column(Json, default=dict)  # parse_ms, reused
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    """Every event a run streamed, in order. A reconnecting client is replayed from here, then follows live."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Step(Base):
    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ts_offset_ms: Mapped[int] = mapped_column(Integer, default=0)


class Field(Base):
    __tablename__ = "fields"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    value_json: Mapped[dict | list | str | float | None] = mapped_column(Json)
    source_quote: Mapped[str | None] = mapped_column(Text)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list | None] = mapped_column(Json)  # union of bboxes
    bboxes: Mapped[list | None] = mapped_column(Json)  # one box per matched line
    confidence: Mapped[float | None] = mapped_column(Float)
    flags: Mapped[list[str]] = mapped_column(StrArray, default=list)


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String)
    due_date: Mapped[date | None]
    description: Mapped[str] = mapped_column(Text)
    source_field_ids: Mapped[list[int]] = mapped_column(IntArray, default=list)


class ProposedAction(Base):
    __tablename__ = "proposed_actions"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String)
    args_json: Mapped[dict] = mapped_column(Json)
    source_field_ids: Mapped[list[int]] = mapped_column(IntArray, default=list)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|approved|rejected|executed|failed
    policy_reason: Mapped[str | None] = mapped_column(Text)
    group_key: Mapped[str | None] = mapped_column(String)  # actions sharing a key are shown together
    group_mode: Mapped[str | None] = mapped_column(String)  # bundle | exclusive (approving one rejects the rest)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionResult(Base):
    __tablename__ = "action_results"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("proposed_actions.id", ondelete="CASCADE"), index=True)
    adapter: Mapped[str] = mapped_column(String)  # live | sandbox
    response_json: Mapped[dict] = mapped_column(Json)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandboxAction(Base):
    """What the sandbox adapters 'did': full args plus a fake response. Nothing leaves the database."""

    __tablename__ = "sandbox_actions"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("proposed_actions.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(String)
    args_json: Mapped[dict] = mapped_column(Json)
    response_json: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageLedger(Base):
    """Rate limit and budget bookkeeping: one row per accepted upload (kind 'upload') and per run's LLM spend ('llm')."""

    __tablename__ = "usage_ledger"
    __table_args__ = (Index("ix_usage_ledger_kind_created_at", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True)
    kind: Mapped[str] = mapped_column(String)  # upload | llm
    ip_hash: Mapped[str | None] = mapped_column(String(64))  # sha256 of the client IP; never the IP itself
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
