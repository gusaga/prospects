"""Evidence normalization, provenance persistence, and freshness checks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlsplit

import httpx
import tldextract
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, EvidenceRecord, Prospect
from .schemas import EvidenceClaim, SourceType


_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_domain(value: str) -> str:
    """Return a registrable domain from a URL or raw hostname."""
    raw = value.strip().casefold()
    if "://" not in raw:
        raw = f"https://{raw}"
    hostname = (urlsplit(raw).hostname or "").removeprefix("www.")
    extracted = _TLD_EXTRACT(hostname)
    return extracted.top_domain_under_public_suffix or hostname


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid source URL: {url!r}")
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{path}" + (f"?{parsed.query}" if parsed.query else "")


def claim_fingerprint(value: str, excerpt: str) -> str:
    payload = f"{normalize_text(value)}\n{normalize_text(excerpt)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def classify_source(url: str, company_domain: str | None = None, declared: SourceType | str | None = None) -> SourceType:
    """Classify transparent source reliability without a third-party data service."""
    domain = normalize_domain(url)
    if company_domain and domain == normalize_domain(company_domain):
        return SourceType.OFFICIAL
    if declared and declared != SourceType.UNKNOWN:
        return SourceType(declared)
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return SourceType.GOV_EDU
    path = urlsplit(url).path.casefold()
    if any(token in path for token in ("press", "newsroom", "media-release", "announcement")):
        return SourceType.PRESS_RELEASE
    if any(token in domain for token in ("directory", "zoominfo", "apollo", "rocketreach")):
        return SourceType.DIRECTORY
    return SourceType.UNKNOWN


def merge_unique_urls(*url_groups: Iterable[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for group in url_groups:
        if not group:
            continue
        for raw in group:
            try:
                url = normalize_url(raw)
            except ValueError:
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def persist_claims(
    session: Session,
    claims: Iterable[EvidenceClaim],
    *,
    company: Company | None = None,
    prospect: Prospect | None = None,
) -> list[EvidenceRecord]:
    """Persist only unique field/source/value claims for an entity."""
    records: list[EvidenceRecord] = []
    for claim in claims:
        try:
            source_url = normalize_url(claim.source_url)
        except ValueError:
            continue
        existing = session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.company_id == (company.id if company else None),
                EvidenceRecord.prospect_id == (prospect.id if prospect else None),
                EvidenceRecord.field_name == claim.field_name,
                EvidenceRecord.source_url == source_url,
                EvidenceRecord.value == claim.value,
            )
        )
        if existing:
            records.append(existing)
            continue
        source_type = classify_source(source_url, company.canonical_domain if company else None, claim.source_type)
        record = EvidenceRecord(
            company_id=company.id if company else None,
            prospect_id=prospect.id if prospect else None,
            field_name=claim.field_name,
            value=claim.value,
            supporting_excerpt=claim.supporting_excerpt,
            source_url=source_url,
            registered_domain=normalize_domain(source_url),
            source_type=source_type.value,
            content_fingerprint=claim_fingerprint(claim.value, claim.supporting_excerpt),
            extracted_at=claim.extracted_at,
            verified=source_type == SourceType.OFFICIAL,
        )
        session.add(record)
        records.append(record)
    session.flush()
    return records


def mark_expired_claims(session: Session, stale_after_days: int, *, now: datetime | None = None) -> int:
    now = now or utc_now()
    cutoff = now - timedelta(days=stale_after_days)
    records = session.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.extracted_at < cutoff,
            EvidenceRecord.freshness_status == "fresh",
        )
    ).all()
    for record in records:
        record.freshness_status = "stale"
    return len(records)


@dataclass(frozen=True)
class RefreshResult:
    evidence_id: int
    status: str
    detail: str


async def refresh_evidence_record(client: httpx.AsyncClient, record: EvidenceRecord) -> RefreshResult:
    """Check public evidence without replacing user-reviewed evidence.

    A changed page is flagged for review. The original claim remains intact.
    """
    try:
        response = await client.get(record.source_url, follow_redirects=True)
    except httpx.HTTPError as exc:
        record.freshness_status = "unreachable"
        return RefreshResult(record.id, "unreachable", str(exc))
    if response.status_code >= 400:
        record.freshness_status = "stale"
        return RefreshResult(record.id, "stale", f"HTTP {response.status_code}")

    page_text = re.sub(r"\s+", " ", response.text).casefold()
    value_present = normalize_text(record.value) in page_text
    excerpt_anchor = normalize_text(record.supporting_excerpt)[:80]
    excerpt_present = bool(excerpt_anchor and excerpt_anchor in page_text)
    record.freshness_status = "fresh" if value_present or excerpt_present else "changed"
    return RefreshResult(record.id, record.freshness_status, "claim text present" if record.freshness_status == "fresh" else "claim text no longer found")
