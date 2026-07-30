"""No-key handoff between the local app and a human-triggered Codex worker."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .agents import AgentRun, AgentRunner
from .config import DATA_DIR, Settings
from .database import session_scope
from .models import Company, Prospect, ResearchRun, ResearchRunProspect
from .orchestrator import ProspectingOrchestrator
from .schemas import (
    AccountDiscoveryOutput,
    CodexResearchResults,
    ContactDiscoveryOutput,
    ICPProfile,
    RapportResearchOutput,
    RunResult,
    TargetAccountType,
)


HANDOFF_DIR = DATA_DIR / "codex-handoffs"
T = TypeVar("T", bound=BaseModel)


def ensure_handoff_directory() -> Path:
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    return HANDOFF_DIR


def queue_codex_run(
    session_factory: sessionmaker[Session],
    icp: ICPProfile,
    settings: Settings | None = None,
    *,
    excluded_domains: Iterable[str] = (),
    continuation_of: str | None = None,
    target_accounts: int | None = None,
    target_qualified_prospects: int | None = None,
) -> str:
    """Create a local run that awaits an explicitly triggered Codex worker."""
    ensure_handoff_directory()
    settings = settings or Settings()
    targets = {
        "target_accounts": target_accounts or settings.max_accounts_per_run,
        "target_qualified_prospects": target_qualified_prospects or settings.target_qualified_prospects_per_run,
        "max_contacts_per_account": settings.max_prospects_per_account,
        "qualified_prospect_alignment_threshold": settings.qualified_prospect_alignment_threshold,
        "excluded_domains": sorted({domain.strip().casefold() for domain in excluded_domains if domain.strip()}),
    }
    if continuation_of:
        targets["continuation_of"] = continuation_of
    with session_scope(session_factory) as session:
        run = ResearchRun(
            icp_json=icp.model_dump(mode="json"),
            status="queued_for_codex",
            metrics={
                "execution_mode": "codex_handoff",
                "accounts_discovered": 0,
                "contacts_discovered": 0,
                "qualified_prospects": 0,
                "shortfall_reasons": [],
                **targets,
            },
        )
        session.add(run)
        session.flush()
        _write_job_file(run)
        return run.id


def _write_job_file(run: ResearchRun) -> Path:
    metrics = run.metrics or {}
    target_accounts = int(metrics.get("target_accounts", 10))
    target_qualified_prospects = int(metrics.get("target_qualified_prospects", 10))
    max_contacts_per_account = int(metrics.get("max_contacts_per_account", 3))
    payload = {
        "run_id": run.id,
        "status": run.status,
        "icp": run.icp_json,
        "max_accounts": target_accounts,
        "max_contacts_per_account": max_contacts_per_account,
        "target_qualified_prospects": target_qualified_prospects,
        "qualified_prospect_alignment_threshold": metrics.get("qualified_prospect_alignment_threshold", 0.45),
        "excluded_domains": metrics.get("excluded_domains", []),
        "continuation_of": metrics.get("continuation_of"),
        "result_schema": "CodexResearchResults",
        "result_file": str(result_path(run.id)),
        "instructions": [
            "Research public no-login sources only. Every claim needs URL, source type, and supporting excerpt.",
            "The account count and qualified-prospect count are delivery targets, not soft ceilings. Continue discovery until both targets are met or public sources are exhausted.",
            "Only return contacts whose published role is a target title, an adjacent persona, or a clear VP/Vice President punctuation or connector variant. Do not fill the quota with unrelated executives.",
            "Use different search queries for each requested geography and relevant ICP phrase. Directory pages may surface candidates, but official company pages must verify every returned account and contact.",
            "If a target cannot be met, include every concrete reason in shortfall_reasons and do not describe the run as complete in the final report.",
        ],
    }
    if run.icp_json.get("target_account_type") == TargetAccountType.OWNER_DEVELOPER.value:
        payload["instructions"].insert(
            3,
            "Return only owner/developers, master developers, or homebuilders. Exclude engineering, surveying, planning, architecture, consulting, and construction-management providers even if they employ a VP of Land Development. Set company.account_type to owner_developer and cite official evidence that the company owns, acquires, entitles, develops, or builds communities.",
        )
    path = job_path(run.id)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def job_path(run_id: str) -> Path:
    return ensure_handoff_directory() / f"{run_id}.job.json"


def result_path(run_id: str) -> Path:
    return ensure_handoff_directory() / f"{run_id}.results.json"


def get_run(session_factory: sessionmaker[Session], run_id: str) -> ResearchRun:
    with session_scope(session_factory) as session:
        run = session.get(ResearchRun, run_id)
        if not run:
            raise ValueError(f"Unknown research run: {run_id}")
        session.expunge(run)
        return run


def list_queued_runs(session_factory: sessionmaker[Session]) -> list[ResearchRun]:
    with session_scope(session_factory) as session:
        runs = list(
            session.scalars(
                select(ResearchRun)
                .where(ResearchRun.status == "queued_for_codex")
                .order_by(ResearchRun.created_at)
            )
        )
        for run in runs:
            session.expunge(run)
        return runs


def queue_gap_fill_run(
    session_factory: sessionmaker[Session], settings: Settings, source_run_id: str
) -> str:
    """Queue an additional public-web job that avoids domains already researched for a short run."""
    source_run = get_run(session_factory, source_run_id)
    if source_run.status not in {"completed", "completed_with_shortfall"}:
        raise ValueError("Only a completed run can be used to create a gap-filling follow-up")
    current_qualified = int((source_run.metrics or {}).get("qualified_prospects", 0))
    target = int((source_run.metrics or {}).get("target_qualified_prospects", settings.target_qualified_prospects_per_run))
    remaining = max(1, target - current_qualified)
    with session_scope(session_factory) as session:
        domains = set(
            session.scalars(
                select(Company.canonical_domain)
                .join(Prospect, Prospect.company_id == Company.id)
                .join(ResearchRunProspect, ResearchRunProspect.prospect_id == Prospect.id)
                .where(ResearchRunProspect.research_run_id == source_run_id)
            )
        )
    return queue_codex_run(
        session_factory,
        ICPProfile.model_validate(source_run.icp_json),
        settings,
        excluded_domains=domains,
        continuation_of=source_run_id,
        target_accounts=min(settings.max_accounts_per_run, max(3, remaining)),
        target_qualified_prospects=remaining,
    )


class CodexResultRunner(AgentRunner):
    """Replay validated Codex output through the normal persistence/scoring pipeline."""

    def __init__(self, results: CodexResearchResults):
        self.results = results

    async def run(self, task: str, output_model: type[T]) -> AgentRun[T]:
        if output_model is AccountDiscoveryOutput:
            output = AccountDiscoveryOutput(accounts=[account.company for account in self.results.accounts])
            return AgentRun(output=output, source_urls=self._all_urls(), steps=0)  # type: ignore[arg-type]
        matching_account = next((account for account in self.results.accounts if account.company.website_url in task), None)
        if output_model is ContactDiscoveryOutput and matching_account:
            output = ContactDiscoveryOutput(
                contacts=[item.contact for item in matching_account.contacts],
                committee_members=matching_account.committee_members,
            )
            urls = [url for item in matching_account.contacts for url in item.contact.source_urls]
            return AgentRun(output=output, source_urls=urls, steps=0)  # type: ignore[arg-type]
        if output_model is RapportResearchOutput and matching_account:
            matching_contact = next((item for item in matching_account.contacts if item.contact.full_name in task), None)
            if matching_contact:
                output = matching_contact.rapport
                urls = [*matching_contact.contact.source_urls, *(signal.source_url for signal in output.rapport_signals)]
                return AgentRun(output=output, source_urls=urls, steps=0)  # type: ignore[arg-type]
        raise ValueError("Result payload does not contain the account/contact requested by the research workflow")

    def _all_urls(self) -> list[str]:
        return [url for account in self.results.accounts for url in account.company.source_urls]


def validate_result_file(run_id: str, file_path: Path) -> CodexResearchResults:
    results = CodexResearchResults.model_validate_json(file_path.read_text(encoding="utf-8"))
    if results.run_id != run_id:
        raise ValueError(f"Result run_id {results.run_id!r} does not match requested run {run_id!r}")
    return results


def ingest_codex_results(
    session_factory: sessionmaker[Session],
    settings: Settings,
    run_id: str,
    file_path: Path | None = None,
) -> RunResult:
    """Validate a Codex-created JSON file and persist it through the standard pipeline."""
    run = get_run(session_factory, run_id)
    if run.status != "queued_for_codex":
        raise ValueError(f"Run {run_id} cannot be ingested from status {run.status!r}")
    results = validate_result_file(run_id, file_path or result_path(run_id))
    icp = ICPProfile.model_validate(run.icp_json)
    run_metrics = run.metrics or {}
    run_settings = settings.model_copy(
        update={
            "max_accounts_per_run": int(run_metrics.get("target_accounts", settings.max_accounts_per_run)),
            "target_qualified_prospects_per_run": int(
                run_metrics.get("target_qualified_prospects", settings.target_qualified_prospects_per_run)
            ),
            "max_prospects_per_account": int(run_metrics.get("max_contacts_per_account", settings.max_prospects_per_account)),
        }
    )
    runner = CodexResultRunner(results)
    result = asyncio.run(ProspectingOrchestrator(session_factory, runner, run_settings).run(icp, existing_run_id=run_id))
    if results.accounts_considered or results.search_queries or results.shortfall_reasons:
        with session_scope(session_factory) as session:
            persisted_run = session.get(ResearchRun, run_id)
            if persisted_run:
                persisted_run.metrics = {
                    **persisted_run.metrics,
                    "accounts_considered": results.accounts_considered,
                    "search_queries": results.search_queries,
                    "worker_shortfall_reasons": results.shortfall_reasons,
                }
                if results.shortfall_reasons and persisted_run.status == "completed":
                    persisted_run.status = "completed_with_shortfall"
                    result = result.model_copy(update={"status": "completed_with_shortfall"})
    return result


def handoff_prompt(run_id: str) -> str:
    return (
        f"Use $prospecting-codex-worker to process queued prospecting run {run_id}. "
        "Read the job's delivery targets. Keep researching until they are met; otherwise record concrete shortfall reasons. "
        "Use public no-login web sources, write cited structured results, validate them, and ingest them into the local database."
    )
