"""Approved-record exports and human-reviewable account briefs."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .evidence import merge_unique_urls, normalize_domain, normalize_text
from .models import Company, EvidenceRecord, Prospect, ResearchRunProspect
from .review import is_suppressed
from .schemas import AccountType


def approved_prospects(session: Session, run_id: str | None = None) -> list[Prospect]:
    statement = (
        select(Prospect)
        .options(joinedload(Prospect.company), joinedload(Prospect.evidence_records))
        .join(Prospect.company)
        .where(Prospect.review_status == "approved")
        .where(Company.account_type != AccountType.PROFESSIONAL_SERVICES.value)
        .order_by(Prospect.confidence_score.desc(), Prospect.full_name)
    )
    if run_id:
        statement = statement.join(ResearchRunProspect).where(ResearchRunProspect.research_run_id == run_id)
    prospects = list(session.scalars(statement).unique())
    return [prospect for prospect in prospects if not is_suppressed(session, prospect)]


def export_rows(session: Session, run_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prospect in approved_prospects(session, run_id):
        run_link = None
        if run_id:
            run_link = session.scalar(
                select(ResearchRunProspect).where(
                    ResearchRunProspect.research_run_id == run_id,
                    ResearchRunProspect.prospect_id == prospect.id,
                )
            )
        rows.append(
            {
                "company": prospect.company.name,
                "company_domain": prospect.company.canonical_domain,
                "full_name": prospect.full_name,
                "role": prospect.role,
                "email": prospect.email or "",
                "phone": prospect.phone or "",
                "profile_url": prospect.profile_url or "",
                "linkedin_url": prospect.linkedin_url or "",
                "owner": prospect.owner or "",
                "outreach_stage": prospect.outreach_stage,
                "next_action_at": prospect.next_action_at.isoformat() if prospect.next_action_at else "",
                "last_activity_at": prospect.last_activity_at.isoformat() if prospect.last_activity_at else "",
                "confidence_score": prospect.confidence_score,
                "icp_alignment_score": run_link.icp_alignment_score if run_link else "",
                "source_urls": " | ".join(prospect.source_urls),
                "rapport_signals": json.dumps(prospect.rapport_signals),
            }
        )
    return rows


def export_csv(session: Session, run_id: str | None = None) -> bytes:
    rows = export_rows(session, run_id)
    stream = io.StringIO()
    fieldnames = [
        "company", "company_domain", "full_name", "role", "email", "phone", "profile_url", "linkedin_url",
        "owner", "outreach_stage", "next_action_at", "last_activity_at", "confidence_score",
        "icp_alignment_score", "source_urls", "rapport_signals",
    ]
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def export_json(session: Session, run_id: str | None = None) -> bytes:
    return json.dumps(export_rows(session, run_id), indent=2).encode("utf-8")


def account_brief(session: Session, company: Company) -> str:
    prospects = list(session.scalars(select(Prospect).where(Prospect.company_id == company.id).order_by(Prospect.confidence_score.desc())))
    signals = company.account_signals
    committee = company.committee_members
    lines = [
        f"# {company.name}",
        "",
        f"- Website: {company.website_url or company.canonical_domain}",
        f"- Industry: {company.industry or 'Not established'}",
        f"- Company size: {company.company_size_band or 'Not established'}",
        "",
        "## Public account signals",
    ]
    if signals:
        lines.extend(f"- **{signal.kind}** — {signal.description} ([source]({signal.source_url}))" for signal in signals)
    else:
        lines.append("- No cited account signals found.")
    lines.extend(["", "## Buying committee"])
    if committee:
        lines.extend(f"- **{member.committee_role.replace('_', ' ')}:** {member.full_name}, {member.role}" for member in committee)
    else:
        lines.append("- No public committee mapping found.")
    lines.extend(["", "## Approved prospects"])
    for prospect in prospects:
        if prospect.review_status != "approved":
            continue
        lines.append(f"- **{prospect.full_name}**, {prospect.role} — confidence {prospect.confidence_score:.2f}")
        lines.extend(f"  - {signal.get('summary', signal)}" for signal in prospect.rapport_signals)
    return "\n".join(lines)


def message_angles(prospect: Prospect) -> list[str]:
    """Produce review-only angles from cited professional context; never send a message."""
    angles = [f"Connect the ICP pain point to {prospect.company.name}'s public priorities."]
    for signal in prospect.rapport_signals[:2]:
        if isinstance(signal, dict) and signal.get("summary"):
            angles.append(f"Reference this public professional context: {signal['summary']}")
    return angles


def import_prospects_csv(session: Session, content: bytes) -> dict[str, int]:
    """Import local CRM-style CSV rows using company-domain/name deduplication."""
    from .models import Company, Prospect

    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    counters = defaultdict(int)
    for row in reader:
        name = (row.get("full_name") or "").strip()
        role = (row.get("role") or "").strip()
        domain = (row.get("company_domain") or "").strip()
        company_name = (row.get("company") or domain).strip()
        if not (name and role and domain):
            counters["skipped"] += 1
            continue
        canonical_domain = normalize_domain(domain)
        company = session.scalar(select(Company).where(Company.canonical_domain == canonical_domain))
        if not company:
            company = Company(name=company_name, canonical_domain=canonical_domain, website_url=f"https://{canonical_domain}")
            session.add(company)
            session.flush()
        identity_key = normalize_text(name)
        prospect = session.scalar(
            select(Prospect).where(Prospect.company_id == company.id, Prospect.identity_key == identity_key)
        )
        if prospect:
            counters["duplicates"] += 1
            continue
        prospect = Prospect(
            company=company,
            full_name=name,
            identity_key=identity_key,
            role=role,
            normalized_role=normalize_text(role),
            email=(row.get("email") or None),
            phone=(row.get("phone") or None),
            profile_url=(row.get("profile_url") or None),
            owner=(row.get("owner") or None),
            confidence_score=float(row.get("confidence_score") or 0.0),
            source_urls=merge_unique_urls((row.get("source_urls") or "").split("|")),
        )
        session.add(prospect)
        counters["created"] += 1
    session.flush()
    return dict(counters)
