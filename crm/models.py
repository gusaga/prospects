"""SQLite schema: companies, prospects, append-only activities, settings,
import batches, and the near-duplicate review queue."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Ordered to match the cold-call workflow; slugs are stored, labels shown.
STATUSES: dict[str, str] = {
    "new": "New",
    "queued": "Queued",
    "no_answer": "Called — no answer",
    "conversation": "Called — conversation",
    "follow_up": "Follow-up",
    "meeting": "Meeting booked",
    "not_fit": "Not a fit",
    "do_not_contact": "Do not contact",
}

# Statuses that never appear in the call queue.
CLOSED_STATUSES = {"not_fit", "do_not_contact"}

PRIORITIES: dict[int, str] = {3: "High", 2: "Normal", 1: "Low"}

SOURCES = ("codex", "csv", "manual", "legacy", "seed")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Unique when present; SQLite allows many NULLs under a unique index.
    domain: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(2048))
    industry: Mapped[str | None] = mapped_column(String(160))
    size_band: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(160))

    prospects: Mapped[list["Prospect"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Prospect(TimestampMixin, Base):
    __tablename__ = "prospects"
    __table_args__ = (UniqueConstraint("company_id", "name_key", name="uq_prospect_company_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(320))
    linkedin_url: Mapped[str | None] = mapped_column(String(2048))
    region: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(160))
    # Other public profiles found by enrichment: [{"label": str, "url": str}]
    profiles: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    icp_score: Mapped[int | None] = mapped_column(Integer)  # 0–100
    icp_rationale: Mapped[str | None] = mapped_column(String(500))
    # List of {"url": str, "note": str|None} source links backing the record.
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=2, index=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_followup_on: Mapped[date | None] = mapped_column(Date, index=True)

    company: Mapped[Company] = relationship(back_populates="prospects")
    activities: Mapped[list["Activity"]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan", order_by="Activity.created_at.desc()"
    )
    # Reviews that point at this prospect die with it.
    dupe_reviews: Mapped[list["DupeReview"]] = relationship(
        back_populates="existing_prospect", cascade="all, delete-orphan"
    )


class Activity(Base):
    """Append-only log: call outcomes, notes, status changes. Never edited."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # call | note | status | system
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    prospect: Mapped[Prospect] = relationship(back_populates="activities")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ResearchRequest(Base):
    """One ask handed to the research agent: 'find me N prospects'.

    Deposits reference the request id, so the app can show how much of the
    ask was actually delivered and why the agent fell short.
    """

    __tablename__ = "research_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="discover", nullable=False)  # discover | enrich
    # For enrich requests: the prospect ids the agent was asked to deepen.
    target_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    requested_count: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    region_focus: Mapped[str | None] = mapped_column(String(160))
    title_focus: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True, nullable=False)
    shortfall: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("research_requests.id"), index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enriched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DupeReview(Base):
    """An incoming record that nearly matches an existing prospect; parked
    here until a human decides merge / keep both / discard."""

    __tablename__ = "dupe_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    existing_prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(16))  # merged | kept_both | discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    existing_prospect: Mapped[Prospect] = relationship(back_populates="dupe_reviews")
