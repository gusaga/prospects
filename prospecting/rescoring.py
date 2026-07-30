"""Recalculate stored ICP alignment after scoring rules change without re-researching the web."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .database import session_scope
from .models import Prospect, ResearchRun, ResearchRunProspect
from .review import feedback_adjustment, is_suppressed
from .scoring import SCORE_VERSION, calculate_confidence_score, calculate_icp_alignment
from .schemas import ICPProfile, RunResult


def _prospect_score_data(prospect: Prospect) -> dict[str, Any]:
    return {
        "full_name": prospect.full_name,
        "role": prospect.role,
        "company_domain": prospect.company.canonical_domain,
        "email": prospect.email,
        "phone": prospect.phone,
        "profile_url": prospect.profile_url,
        "rapport_signals": prospect.rapport_signals,
        "evidence": [
            {"field_name": record.field_name, "source_url": record.source_url, "source_type": record.source_type}
            for record in prospect.evidence_records
        ],
    }


def rescore_run(session_factory: sessionmaker[Session], settings: Settings, run_id: str) -> RunResult:
    """Refresh confidence/alignment and turn legacy partial runs into explicit shortfalls."""
    with session_scope(session_factory) as session:
        run = session.get(ResearchRun, run_id)
        if not run:
            raise ValueError(f"Unknown research run: {run_id}")
        if run.status in {"queued", "queued_for_codex", "running", "cancelled", "failed"}:
            raise ValueError(f"Run {run_id} is not finished and cannot be rescored")
        icp = ICPProfile.model_validate(run.icp_json)
        links = list(
            session.scalars(
                select(ResearchRunProspect).where(ResearchRunProspect.research_run_id == run_id)
            )
        )
        for link in links:
            prospect = link.prospect
            company = prospect.company
            prospect.confidence_score = calculate_confidence_score(_prospect_score_data(prospect), prospect.source_urls)
            prospect.score_version = SCORE_VERSION
            alignment = calculate_icp_alignment(prospect, company, icp, company.account_signals)
            bucket = "target" if any(reason.startswith("target role") for reason in alignment.reasons) else "adjacent"
            adjustment, adjustment_reason = feedback_adjustment(session, bucket, settings.feedback_minimum_reviews)
            link.icp_alignment_score = round(min(1.0, max(0.0, alignment.score + adjustment)), 3)
            link.feedback_adjustment = adjustment
            link.alignment_reasons = [*alignment.reasons, adjustment_reason]
            link.is_suppressed = is_suppressed(session, prospect)

        target_accounts = int((run.metrics or {}).get("target_accounts", settings.max_accounts_per_run))
        target_prospects = int(
            (run.metrics or {}).get("target_qualified_prospects", settings.target_qualified_prospects_per_run)
        )
        qualified = [
            link
            for link in links
            if not link.is_suppressed
            and link.icp_alignment_score >= settings.qualified_prospect_alignment_threshold
        ]
        accounts = {link.prospect.company_id for link in links}
        shortfalls: list[str] = []
        if len(accounts) < target_accounts:
            shortfalls.append(f"Account target shortfall: found {len(accounts)} of {target_accounts} accounts.")
        if len(qualified) < target_prospects:
            shortfalls.append(
                "Qualified prospect target shortfall: "
                f"found {len(qualified)} of {target_prospects} at ICP alignment "
                f">= {settings.qualified_prospect_alignment_threshold:.2f}."
            )
        run.metrics = {
            **(run.metrics or {}),
            "target_accounts": target_accounts,
            "target_qualified_prospects": target_prospects,
            "qualified_prospect_alignment_threshold": settings.qualified_prospect_alignment_threshold,
            "accounts_discovered": len(accounts),
            "contacts_discovered": len(links),
            "qualified_prospects": len(qualified),
            "unqualified_prospects": max(0, len(links) - len(qualified)),
            "corroborated_prospects": sum(1 for link in links if link.prospect.confidence_score > 0.85),
            "shortfall_reasons": shortfalls,
            "rescored_with": SCORE_VERSION,
        }
        run.status = "completed_with_shortfall" if shortfalls else "completed"
        return RunResult(run_id=run.id, status=run.status, metrics=run.metrics, errors=run.errors)
