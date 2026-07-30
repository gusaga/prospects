"""The deposit pipeline: validate -> dedupe -> insert/enrich/park/reject.

Used by every way records enter the system: JSON deposits from the research
agent, CSV imports, and manual adds. Every rejected record is written to
data/rejects.jsonl with the reason, so nothing disappears silently.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from . import config
from .dedupe import (
    find_company,
    find_exact_prospect,
    find_near_duplicate,
    name_key,
    normalize_domain,
)
from .models import STATUSES, Activity, Company, DupeReview, ImportBatch, Prospect

SCHEMA_VERSION = 1

# Fields an exact duplicate may fill in when the existing value is empty.
ENRICHABLE_FIELDS = ("title", "phone", "email", "linkedin_url", "region", "icp_score", "icp_rationale")


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=2048)
    industry: str | None = Field(default=None, max_length=160)
    size_band: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=160)


class EvidenceLink(BaseModel):
    url: str = Field(min_length=10, max_length=2048)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("url")
    @classmethod
    def must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("evidence url must start with http:// or https://")
        return value


class ProspectRecord(BaseModel):
    """One prospect as deposited by the research agent (or a CSV row)."""

    company: CompanyIn
    full_name: str = Field(min_length=2, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=320)
    linkedin_url: str | None = Field(default=None, max_length=2048)
    region: str | None = Field(default=None, max_length=160)
    icp_score: int | None = Field(default=None, ge=0, le=100)
    icp_rationale: str | None = Field(default=None, max_length=500)
    evidence: list[EvidenceLink] = Field(default_factory=list, max_length=12)
    notes: str | None = Field(default=None, max_length=4000)
    status: str = "new"
    priority: int = Field(default=2, ge=1, le=3)
    next_followup_on: date | None = None

    @field_validator("email")
    @classmethod
    def email_shape(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            raise ValueError("email does not look like an address")
        return value

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        if value not in STATUSES:
            raise ValueError(f"unknown status {value!r}; expected one of {', '.join(STATUSES)}")
        return value

    @field_validator("phone", "title", "region", "icp_rationale", "notes", "full_name")
    @classmethod
    def strip_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class DepositFile(BaseModel):
    """Versioned envelope the research agent writes into inbox/."""

    schema_version: Literal[1]
    source: str = Field(default="codex", max_length=16)
    batch_note: str | None = Field(default=None, max_length=1000)
    prospects: list[ProspectRecord] = Field(min_length=1, max_length=500)


@dataclass
class IngestSummary:
    filename: str
    source: str
    created: int = 0
    enriched: int = 0
    duplicates: int = 0
    review: int = 0
    rejected: int = 0
    batch_id: int | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.enriched + self.duplicates + self.review + self.rejected

    def one_line(self) -> str:
        return (
            f"{self.total} records: {self.created} created, {self.enriched} enriched, "
            f"{self.duplicates} duplicates skipped, {self.review} parked for duplicate review, "
            f"{self.rejected} rejected"
        )


def _reject(summary: IngestSummary, raw: Any, reason: str) -> None:
    summary.rejected += 1
    summary.messages.append(f"REJECTED: {reason}")
    config.ensure_dirs()
    entry = {
        "rejected_at": datetime.now(timezone.utc).isoformat(),
        "file": summary.filename,
        "reason": reason,
        "record": raw,
    }
    with config.REJECTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")


def _get_or_create_company(session: Session, data: CompanyIn) -> Company:
    domain = normalize_domain(data.domain or data.website)
    company = find_company(session, domain=domain, name=data.name)
    if company:
        # Fill anything the existing row is missing.
        for attr, value in (
            ("domain", domain), ("website", data.website), ("industry", data.industry),
            ("size_band", data.size_band), ("region", data.region),
        ):
            if value and not getattr(company, attr):
                setattr(company, attr, value)
        return company
    website = data.website or (f"https://{domain}" if domain else None)
    company = Company(
        name=data.name.strip(), domain=domain, website=website,
        industry=data.industry, size_band=data.size_band, region=data.region,
    )
    session.add(company)
    session.flush()
    return company


def _enrich(session: Session, existing: Prospect, record: ProspectRecord) -> bool:
    filled: list[str] = []
    for attr in ENRICHABLE_FIELDS:
        incoming = getattr(record, attr)
        if incoming not in (None, "") and getattr(existing, attr) in (None, ""):
            setattr(existing, attr, incoming)
            filled.append(attr)
    known_urls = {link.get("url", "").rstrip("/") for link in existing.evidence}
    new_links = [
        {"url": link.url, "note": link.note}
        for link in record.evidence
        if link.url.rstrip("/") not in known_urls
    ]
    if new_links and len(existing.evidence) < 12:
        existing.evidence = existing.evidence + new_links[: 12 - len(existing.evidence)]
        filled.append("evidence")
    if filled:
        session.add(Activity(
            prospect_id=existing.id, kind="system",
            body=f"Enriched from import: {', '.join(filled)}",
        ))
    return bool(filled)


def ingest_records(
    session: Session,
    records: list[dict[str, Any]],
    *,
    filename: str,
    source: str,
) -> IngestSummary:
    """Run every raw record through validate -> dedupe -> persist."""
    summary = IngestSummary(filename=filename, source=source)
    batch = ImportBatch(filename=filename, source=source)
    session.add(batch)
    session.flush()

    for index, raw in enumerate(records, start=1):
        try:
            record = ProspectRecord.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            _reject(summary, raw, f"record {index}: {problems}")
            continue

        company = _get_or_create_company(session, record.company)

        exact = find_exact_prospect(session, company.id, record.full_name)
        if exact:
            if _enrich(session, exact, record):
                summary.enriched += 1
            else:
                summary.duplicates += 1
            continue

        near = find_near_duplicate(
            session,
            company_id=company.id,
            full_name=record.full_name,
            email=record.email,
            phone=record.phone,
            linkedin_url=record.linkedin_url,
        )
        if near:
            existing, reason = near
            session.add(DupeReview(
                batch_id=batch.id,
                payload=record.model_dump(mode="json"),
                existing_prospect_id=existing.id,
                reason=reason,
            ))
            summary.review += 1
            continue

        prospect = Prospect(
            company_id=company.id,
            full_name=record.full_name,
            name_key=name_key(record.full_name),
            title=record.title,
            phone=record.phone,
            email=record.email,
            linkedin_url=record.linkedin_url,
            region=record.region or company.region,
            icp_score=record.icp_score,
            icp_rationale=record.icp_rationale,
            evidence=[link.model_dump() for link in record.evidence],
            status=record.status,
            priority=record.priority,
            notes=record.notes,
            source=source,
            next_followup_on=record.next_followup_on,
        )
        session.add(prospect)
        session.flush()
        session.add(Activity(prospect_id=prospect.id, kind="system", body=f"Added via {source} import ({filename})"))
        summary.created += 1

    batch.created_count = summary.created
    batch.enriched_count = summary.enriched
    batch.duplicate_count = summary.duplicates
    batch.review_count = summary.review
    batch.rejected_count = summary.rejected
    summary.batch_id = batch.id
    return summary


def ingest_deposit_json(session: Session, content: str, *, filename: str) -> IngestSummary:
    """Validate a deposit file (the envelope) and ingest its records."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        summary = IngestSummary(filename=filename, source="codex")
        _reject(summary, content[:2000], f"not valid JSON: {exc}")
        return summary
    try:
        deposit = DepositFile.model_validate(parsed)
    except ValidationError as exc:
        summary = IngestSummary(filename=filename, source="codex")
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:5]
        )
        _reject(summary, parsed, f"deposit envelope invalid: {problems}")
        return summary
    return ingest_records(
        session,
        [record.model_dump(mode="json") for record in deposit.prospects],
        filename=filename,
        source=deposit.source if deposit.source in ("codex", "csv", "manual") else "codex",
    )


CSV_COLUMNS = [
    "company", "company_domain", "full_name", "title", "phone", "email",
    "linkedin_url", "region", "industry", "size_band", "icp_score",
    "icp_rationale", "status", "priority", "notes", "next_followup_on",
    "evidence_urls",
]


def csv_row_to_record(row: dict[str, str]) -> dict[str, Any]:
    def clean(key: str) -> str | None:
        return (row.get(key) or "").strip() or None

    evidence = [
        {"url": url.strip()}
        for url in (row.get("evidence_urls") or "").split("|")
        if url.strip()
    ]
    record: dict[str, Any] = {
        "company": {
            "name": clean("company") or clean("company_domain") or "",
            "domain": clean("company_domain"),
            "industry": clean("industry"),
            "size_band": clean("size_band"),
            "region": clean("region"),
        },
        "full_name": clean("full_name") or "",
        "title": clean("title"),
        "phone": clean("phone"),
        "email": clean("email"),
        "linkedin_url": clean("linkedin_url"),
        "region": clean("region"),
        "icp_rationale": clean("icp_rationale"),
        "notes": clean("notes"),
        "evidence": evidence,
    }
    if clean("icp_score"):
        record["icp_score"] = clean("icp_score")
    if clean("status"):
        record["status"] = clean("status")
    if clean("priority"):
        record["priority"] = clean("priority")
    if clean("next_followup_on"):
        record["next_followup_on"] = clean("next_followup_on")
    return record


def ingest_csv(session: Session, content: bytes, *, filename: str) -> IngestSummary:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    records = [csv_row_to_record(row) for row in reader]
    return ingest_records(session, records, filename=filename, source="csv")
