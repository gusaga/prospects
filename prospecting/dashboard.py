"""Read-only view models for the local Streamlit prospecting dashboard."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .account_types import account_type_label
from .models import AccountSignal, Company, EvidenceRecord, Prospect, ResearchRun, ResearchRunProspect
from .outreach import outreach_stage_label
from .schemas import AccountType


@dataclass(frozen=True)
class DashboardSnapshot:
    """A serializable, UI-focused projection of the local prospecting ledger."""

    totals: dict[str, int]
    runs: list[dict[str, Any]]
    accounts: list[dict[str, Any]]
    prospects: list[dict[str, Any]]


def build_dashboard_snapshot(session: Session) -> DashboardSnapshot:
    """Build one consistent dashboard view without mutating prospecting data."""
    runs = list(session.scalars(select(ResearchRun).order_by(ResearchRun.created_at.desc())))
    companies = list(session.scalars(select(Company).order_by(Company.name)))
    prospects = list(
        session.scalars(
            select(Prospect)
            .options(joinedload(Prospect.company))
            .order_by(Prospect.confidence_score.desc(), Prospect.full_name)
        ).unique()
    )
    links = list(session.scalars(select(ResearchRunProspect)))

    company_evidence = Counter(
        session.scalars(select(EvidenceRecord.company_id).where(EvidenceRecord.company_id.is_not(None)))
    )
    prospect_evidence = Counter(
        session.scalars(select(EvidenceRecord.prospect_id).where(EvidenceRecord.prospect_id.is_not(None)))
    )
    account_signal_counts = Counter(
        session.scalars(select(AccountSignal.company_id))
    )
    contacts_per_company = Counter(prospect.company_id for prospect in prospects)
    links_by_prospect: dict[int, list[ResearchRunProspect]] = defaultdict(list)
    for link in links:
        links_by_prospect[link.prospect_id].append(link)

    run_rows = [
        {
            "id": run.id,
            "label": f"{run.id[:8]} — {run.status}",
            "status": run.status,
            "created_at": run.created_at.isoformat(timespec="seconds"),
            "finished_at": run.finished_at.isoformat(timespec="seconds") if run.finished_at else None,
            "metrics": dict(run.metrics or {}),
            "errors": list(run.errors or []),
        }
        for run in runs
    ]

    account_rows = [
        {
            "id": company.id,
            "name": company.name,
            "domain": company.canonical_domain,
            "website_url": company.website_url,
            "industry": company.industry or "Not established",
            "geography": company.geography or "Not established",
            "company_size_band": company.company_size_band or "Not established",
            "account_type": company.account_type or AccountType.UNKNOWN.value,
            "account_type_label": account_type_label(company.account_type),
            "contacts": contacts_per_company[company.id],
            "evidence_records": company_evidence[company.id],
            "account_signals": account_signal_counts[company.id],
        }
        for company in companies
    ]

    prospect_rows: list[dict[str, Any]] = []
    for prospect in prospects:
        prospect_links = links_by_prospect.get(prospect.id, [])
        best_link = max(prospect_links, key=lambda item: item.icp_alignment_score, default=None)
        alignment_by_run = {link.research_run_id: round(link.icp_alignment_score, 3) for link in prospect_links}
        prospect_rows.append(
            {
                "id": prospect.id,
                "company_id": prospect.company_id,
                "company": prospect.company.name,
                "domain": prospect.company.canonical_domain,
                "industry": prospect.company.industry or "Not established",
                "geography": prospect.company.geography or "Not established",
                "account_type": prospect.company.account_type or AccountType.UNKNOWN.value,
                "account_type_label": account_type_label(prospect.company.account_type),
                "full_name": prospect.full_name,
                "role": prospect.role,
                "confidence": round(prospect.confidence_score, 3),
                "review_status": prospect.review_status,
                "public_contact": bool(prospect.email or prospect.phone or prospect.profile_url),
                "linkedin_url": prospect.linkedin_url,
                "outreach_stage": prospect.outreach_stage,
                "outreach_stage_label": outreach_stage_label(prospect.outreach_stage),
                "next_action_at": (
                    prospect.next_action_at.isoformat(timespec="seconds") if prospect.next_action_at else None
                ),
                "evidence_records": prospect_evidence[prospect.id],
                "source_count": len(prospect.source_urls or []),
                "signal_count": len(prospect.rapport_signals or []),
                "run_ids": [link.research_run_id for link in prospect_links],
                "alignment_by_run": alignment_by_run,
                "best_alignment": round(best_link.icp_alignment_score, 3) if best_link else None,
                "best_alignment_reasons": list(best_link.alignment_reasons or []) if best_link else [],
                "suppressed": bool(best_link.is_suppressed) if best_link else False,
            }
        )

    reviewable = sum(item.review_status in {"pending", "needs_review"} for item in prospects)
    direct_accounts = sum(company.account_type == AccountType.OWNER_DEVELOPER.value for company in companies)
    partner_accounts = sum(company.account_type == AccountType.PROFESSIONAL_SERVICES.value for company in companies)
    direct_prospects = sum(item["account_type"] == AccountType.OWNER_DEVELOPER.value for item in prospect_rows)
    partner_prospects = sum(item["account_type"] == AccountType.PROFESSIONAL_SERVICES.value for item in prospect_rows)
    approved = sum(item["review_status"] == "approved" for item in prospect_rows)
    needs_linkedin = sum(item["outreach_stage"] == "find_on_linkedin" for item in prospect_rows)
    active_outreach = sum(
        item["outreach_stage"] in {"linkedin_found", "message_drafted", "message_sent", "replied", "nurture"}
        for item in prospect_rows
    )
    demos_booked = sum(item["outreach_stage"] == "demo_booked" for item in prospect_rows)
    published_contacts = sum(item["public_contact"] for item in prospect_rows)
    totals = {
        "accounts": len(companies),
        "prospects": len(prospects),
        "direct_accounts": direct_accounts,
        "direct_prospects": direct_prospects,
        "partner_accounts": partner_accounts,
        "partner_prospects": partner_prospects,
        "research_runs": len(runs),
        "reviewable": reviewable,
        "approved": approved,
        "needs_linkedin": needs_linkedin,
        "active_outreach": active_outreach,
        "demos_booked": demos_booked,
        "published_contacts": published_contacts,
        "evidence_records": len(list(session.scalars(select(EvidenceRecord.id)))),
    }
    return DashboardSnapshot(totals=totals, runs=run_rows, accounts=account_rows, prospects=prospect_rows)
