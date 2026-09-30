from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, UTC
from typing import Any, Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Naive UTC (SQLite-friendly). All timestamps in this API are UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def _pk():
    return mapped_column(String(36), primary_key=True, default=uid)


def _fk(table: str, nullable: bool = False, index: bool = True):
    return mapped_column(String(36), ForeignKey(f"{table}.id"), nullable=nullable, index=index)


# ───────────────────────── identity ─────────────────────────
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="site_engineer")
    language: Mapped[str] = mapped_column(String(5), default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RevokedToken(Base):
    """Logout denylist (jti). Rows are only needed until the token would have expired anyway."""
    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    exp: Mapped[datetime] = mapped_column(DateTime, index=True)


class RefreshToken(Base):
    """Opaque, single-use refresh token (only its SHA-256 is stored). Rotated on every use; replaying an old one revokes the family."""
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users")
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    client_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    user_id: Mapped[str] = _fk("users")


# ───────────────────────── location hierarchy ─────────────────────────
class Building(Base):
    __tablename__ = "buildings"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    name: Mapped[str] = mapped_column(String(120))


class Floor(Base):
    __tablename__ = "floors"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    building_id: Mapped[str] = _fk("buildings")
    name: Mapped[str] = mapped_column(String(120))
    level: Mapped[int] = mapped_column(Integer, default=0)


class Element(Base):
    __tablename__ = "elements"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    floor_id: Mapped[str] = _fk("floors")
    name: Mapped[str] = mapped_column(String(120))
    element_type: Mapped[str] = mapped_column(String(30), default="slab")
    exposure: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ───────────────────────── material passport & testing ─────────────────────────
class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    batch_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    # Unguessable token used in the public QR URL, so sequential batch codes cannot be enumerated by outsiders.
    public_token: Mapped[str] = mapped_column(String(24), unique=True, index=True, default=lambda: secrets.token_urlsafe(9))
    material: Mapped[str] = mapped_column(String(30), default="concrete")
    construction_type: Mapped[str] = mapped_column(String(10), default="rcc")  # rcc | pcc
    grade: Mapped[str] = mapped_column(String(6))
    fck: Mapped[float] = mapped_column(Float)
    exposure: Mapped[str] = mapped_column(String(20), default="moderate")
    supplier: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    supplier_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    delivery_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    quantity_m3: Mapped[float] = mapped_column(Float, default=0)
    cement_type: Mapped[str] = mapped_column(String(10), default="opc")
    cement_content_kg_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wc_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    admixture: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    mix_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registration_check: Mapped[Any] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (CheckConstraint("status in ('PENDING','VERIFIED','REVIEW_REQUIRED','FLAGGED')", name="ck_batch_status"),
                      CheckConstraint("quantity_m3 >= 0", name="ck_batch_qty"))


class Sample(Base):
    __tablename__ = "samples"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    batch_id: Mapped[str] = _fk("batches")
    sample_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    cast_date: Mapped[date] = mapped_column(Date)
    cubes_cast: Mapped[int] = mapped_column(Integer, default=6)
    curing_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TestRecord(Base):
    """Append-only: corrections create a new version that supersedes the old one."""
    __tablename__ = "test_records"
    __test__ = False
    __table_args__ = (CheckConstraint("status in ('VERIFIED','REVIEW_REQUIRED','FLAGGED')", name="ck_test_status"),)
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    batch_id: Mapped[str] = _fk("batches")
    sample_id: Mapped[Optional[str]] = _fk("samples", nullable=True)
    test_type: Mapped[str] = mapped_column(String(40), index=True)
    age_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    values: Mapped[Any] = mapped_column(JSON, default=dict)
    lab_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="REVIEW_REQUIRED", index=True)
    validation: Mapped[Any] = mapped_column(JSON, default=list)
    review: Mapped[Any] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    record_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BatchUsage(Base):
    __tablename__ = "batch_usages"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    batch_id: Mapped[str] = _fk("batches")
    element_id: Mapped[str] = _fk("elements")
    volume_m3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ───────────────────────── evidence ─────────────────────────
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    batch_id: Mapped[Optional[str]] = _fk("batches", nullable=True)
    sample_id: Mapped[Optional[str]] = _fk("samples", nullable=True)
    test_id: Mapped[Optional[str]] = _fk("test_records", nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="photo")
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(400))
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    uploaded_by: Mapped[str] = _fk("users")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OcrDraft(Base):
    __tablename__ = "ocr_drafts"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    document_id: Mapped[Optional[str]] = _fk("documents", nullable=True)
    provider: Mapped[str] = mapped_column(String(30))
    extracted: Mapped[Any] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|confirmed|rejected
    test_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ───────────────────────── investigation ─────────────────────────
class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    batch_id: Mapped[str] = _fk("batches")
    test_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    closure_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opened_by: Mapped[str] = _fk("users")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class InvestigationAction(Base):
    __tablename__ = "investigation_actions"
    id: Mapped[str] = _pk()
    investigation_id: Mapped[str] = _fk("investigations")
    stage: Mapped[int] = mapped_column(Integer, default=1)
    action_type: Mapped[str] = mapped_column(String(40))
    element_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|done|waived
    result_test_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ───────────────────────── durability ─────────────────────────
class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    element_id: Mapped[str] = _fk("elements")
    kind: Mapped[str] = mapped_column(String(30), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    element_id: Mapped[str] = _fk("elements")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    score: Mapped[float] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(10))
    trend: Mapped[str] = mapped_column(String(20))
    factors: Mapped[Any] = mapped_column(JSON, default=list)
    anomalies: Mapped[Any] = mapped_column(JSON, default=list)
    recommendations: Mapped[Any] = mapped_column(JSON, default=list)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    kind: Mapped[str] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    message: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    element_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    test_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ───────────────────────── AI layer ─────────────────────────
class AiConversation(Base):
    """Owned by exactly one user. Nobody else (not even an admin) can read the messages through the API."""
    __tablename__ = "ai_conversations"
    id: Mapped[str] = _pk()
    user_id: Mapped[str] = _fk("users")
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(120), default="New conversation")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    __table_args__ = (Index("ix_aiconv_user_updated", "user_id", "updated_at"),)


class AiMessage(Base):
    __tablename__ = "ai_messages"
    id: Mapped[str] = _pk()
    conversation_id: Mapped[str] = _fk("ai_conversations")
    role: Mapped[str] = mapped_column(String(12))                     # user | assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[Any] = mapped_column(JSON, default=dict)             # sources, tools used, validation flag (no secrets)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_aimsg_conv_created", "conversation_id", "created_at"),)


class AiUsage(Base):
    """One row per AI request: cost/latency accounting. Never stores prompts, answers, keys or tokens."""
    __tablename__ = "ai_usage"
    id: Mapped[str] = _pk()
    request_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(20))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20))                   # ok | fallback | error | rejected
    error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_aiusage_user_time", "user_id", "created_at"),)


class AiDocument(Base):
    """A project document (specification, method statement) indexed for retrieval. Always tied to one project."""
    __tablename__ = "ai_documents"
    id: Mapped[str] = _pk()
    project_id: Mapped[str] = _fk("projects")
    title: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = _fk("users")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "content_hash"),)


class AiChunk(Base):
    """Retrieval unit. `source`='knowledge' rows are shared IS-rule notes (project_id NULL); 'project_doc' rows are tenant-scoped."""
    __tablename__ = "ai_chunks"
    id: Mapped[str] = _pk()
    source: Mapped[str] = mapped_column(String(20), index=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[Any] = mapped_column(JSON, nullable=True)      # list[float]; pgvector is the upgrade path past ~10k chunks
    embedding_model: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    embedding_dims: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chunk_config: Mapped[str] = mapped_column(String(40), default="p800o100")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ───────────────────────── integrity & sync ─────────────────────────
class AuditLog(Base):
    """Hash-chained, append-only. entry_hash = SHA256(prev_hash + canonical(entry))."""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    actor_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    actor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    details: Mapped[Any] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))


class ChangeLog(Base):
    """Monotonic feed of changes that offline clients pull with a cursor (seq)."""
    __tablename__ = "change_log"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    op: Mapped[str] = mapped_column(String(10))
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SyncOp(Base):
    """Idempotency ledger per user: replaying the same client op_id returns the stored result."""
    __tablename__ = "sync_ops"
    op_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    op_type: Mapped[str] = mapped_column(String(40))
    result: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# Composite indexes justified by the hot queries: current tests per batch/project, observation series per element,
# newest-first lists per project, and the sync pull feed.
Index("ix_test_batch_current", TestRecord.batch_id, TestRecord.is_current)
Index("ix_test_project_current", TestRecord.project_id, TestRecord.is_current)
Index("ix_obs_element_kind_time", Observation.element_id, Observation.kind, Observation.observed_at)
Index("ix_batch_project_created", Batch.project_id, Batch.created_at)
Index("ix_alert_project_created", Alert.project_id, Alert.created_at)
Index("ix_change_project_seq", ChangeLog.project_id, ChangeLog.seq)
Index("ix_risk_element_time", RiskAssessment.element_id, RiskAssessment.computed_at)

