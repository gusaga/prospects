"""SQLite/SQLAlchemy persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_domain: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(2048))
    industry: Mapped[str | None] = mapped_column(String(160))
    company_size_band: Mapped[str | None] = mapped_column(String(80))
    geography: Mapped[str | None] = mapped_column(String(160))
    account_type: Mapped[str] = mapped_column(String(32), default="unknown", server_default="unknown", nullable=False, index=True)
    source_urls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    prospects: Mapped[list["Prospect"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    evidence_records: Mapped[list["EvidenceRecord"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    account_signals: Mapped[list["AccountSignal"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    committee_members: Mapped[list["BuyingCommitteeMember"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Prospect(TimestampMixin, Base):
    __tablename__ = "prospects"
    __table_args__ = (UniqueConstraint("company_id", "identity_key", name="uq_prospect_company_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_role: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(80))
    profile_url: Mapped[str | None] = mapped_column(String(2048))
    owner: Mapped[str | None] = mapped_column(String(255))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    source_urls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    rapport_signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)
    outreach_stage: Mapped[str] = mapped_column(
        String(32), default="awaiting_review", server_default="awaiting_review", index=True, nullable=False
    )
    linkedin_url: Mapped[str | None] = mapped_column(String(2048))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outreach_notes: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship(back_populates="prospects")
    evidence_records: Mapped[list["EvidenceRecord"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")
    run_links: Mapped[list["ResearchRunProspect"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")
    feedback: Mapped[list["ProspectFeedback"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")
    decisions: Mapped[list["ReviewDecision"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")
    outreach_activities: Mapped[list["OutreachActivity"]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan"
    )
    relationship_notes: Mapped[list["RelationshipNote"]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan"
    )


class EvidenceRecord(TimestampMixin, Base):
    __tablename__ = "evidence_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    registered_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(32), default="fresh", index=True, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped[Company | None] = relationship(back_populates="evidence_records")
    prospect: Mapped[Prospect | None] = relationship(back_populates="evidence_records")


class AccountSignal(TimestampMixin, Base):
    __tablename__ = "account_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(32), default="fresh", nullable=False)

    company: Mapped[Company] = relationship(back_populates="account_signals")


class ResearchRun(TimestampMixin, Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    icp_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prospect_links: Mapped[list["ResearchRunProspect"]] = relationship(back_populates="research_run", cascade="all, delete-orphan")
    decisions: Mapped[list["ReviewDecision"]] = relationship(back_populates="research_run")
    feedback: Mapped[list["ProspectFeedback"]] = relationship(back_populates="research_run")


class ResearchRunProspect(Base):
    __tablename__ = "research_run_prospects"
    __table_args__ = (UniqueConstraint("research_run_id", "prospect_id", name="uq_run_prospect"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True, nullable=False)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True, nullable=False)
    icp_alignment_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    feedback_adjustment: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    alignment_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    research_run: Mapped[ResearchRun] = relationship(back_populates="prospect_links")
    prospect: Mapped[Prospect] = relationship(back_populates="run_links")


class BuyingCommitteeMember(TimestampMixin, Base):
    __tablename__ = "buying_committee_members"
    __table_args__ = (UniqueConstraint("company_id", "name_key", "committee_role", name="uq_committee_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    committee_role: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_urls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    company: Mapped[Company] = relationship(back_populates="committee_members")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True, nullable=False)
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id"), index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    prospect: Mapped[Prospect] = relationship(back_populates="decisions")
    research_run: Mapped[ResearchRun | None] = relationship(back_populates="decisions")


class ProspectFeedback(Base):
    __tablename__ = "prospect_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True, nullable=False)
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id"), index=True)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    persona_bucket: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    prospect: Mapped[Prospect] = relationship(back_populates="feedback")
    research_run: Mapped[ResearchRun | None] = relationship(back_populates="feedback")


class OutreachActivity(Base):
    """A durable local timeline for the SDR's manual follow-up work."""

    __tablename__ = "outreach_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prospect: Mapped[Prospect] = relationship(back_populates="outreach_activities")


class RelationshipNote(Base):
    """Small, searchable pieces of professional relationship context for a prospect."""

    __tablename__ = "relationship_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True, nullable=False)
    bucket: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_type: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    prospect: Mapped[Prospect] = relationship(back_populates="relationship_notes")


class SuppressionEntry(TimestampMixin, Base):
    __tablename__ = "suppression_entries"
    __table_args__ = (UniqueConstraint("field_type", "normalized_value", name="uq_suppression_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(512), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
