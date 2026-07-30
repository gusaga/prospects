"""Asynchronous multi-agent workflow with partial persistence and run observability."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .account_types import classify_candidate, evaluate_candidate
from .agents import AgentRunner, ContactDiscoveryAgent, RapportAgent, TargetAccountAgent
from .config import Settings
from .database import session_scope
from .evidence import classify_source, merge_unique_urls, normalize_domain, normalize_text, persist_claims
from .models import (
    AccountSignal,
    BuyingCommitteeMember,
    Company,
    Prospect,
    ResearchRun,
    ResearchRunProspect,
)
from .review import feedback_adjustment, is_suppressed
from .relationship import add_research_note
from .schemas import (
    AccountSignalCandidate,
    AccountType,
    CommitteeCandidate,
    CompanyCandidate,
    ContactCandidate,
    ICPProfile,
    RapportSignalCandidate,
    RunResult,
)
from .scoring import SCORE_VERSION, calculate_icp_alignment, calculate_confidence_score


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


ProgressCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class PersistedCompany:
    id: int
    candidate: CompanyCandidate


@dataclass(frozen=True)
class PersistedProspect:
    id: int
    full_name: str
    role: str
    company: PersistedCompany


class ProspectingOrchestrator:
    def __init__(self, session_factory: sessionmaker[Session], runner: AgentRunner, settings: Settings):
        self.session_factory = session_factory
        self.settings = settings
        self.account_agent = TargetAccountAgent(runner, settings)
        self.contact_agent = ContactDiscoveryAgent(runner, settings)
        self.rapport_agent = RapportAgent(runner, settings)
        self._agent_semaphore = asyncio.Semaphore(settings.max_concurrent_agents)
        self._metrics: dict[str, Any] = {}
        self._errors: list[str] = []
        self._progress: ProgressCallback | None = None

    async def run(
        self,
        icp: ICPProfile,
        progress: ProgressCallback | None = None,
        existing_run_id: str | None = None,
    ) -> RunResult:
        self._metrics = {
            "target_accounts": self.settings.max_accounts_per_run,
            "target_qualified_prospects": self.settings.target_qualified_prospects_per_run,
            "qualified_prospect_alignment_threshold": self.settings.qualified_prospect_alignment_threshold,
            "accounts_discovered": 0,
            "professional_services_excluded": 0,
            "accounts_without_owner_evidence": 0,
            "account_type_exclusions": [],
            "official_site_accounts": 0,
            "contacts_discovered": 0,
            "qualified_prospects": 0,
            "unqualified_prospects": 0,
            "shortfall_reasons": [],
            "corroborated_prospects": 0,
            "suppressed": 0,
            "agent_steps": 0,
            "agent_seconds": 0.0,
            "retries": 0,
            "model_step_budget": self.settings.model_step_budget,
        }
        self._errors = []
        self._progress = progress
        run_id = existing_run_id or self._create_run(icp)
        try:
            self._update_run(run_id, status="running", started_at=utc_now())
            self._emit("discovery", "Searching for target accounts")
            discovery = await self._call(lambda: self.account_agent.discover(icp), run_id)
            companies = []
            for candidate in discovery.output.accounts[: self.settings.max_accounts_per_run]:
                eligibility = evaluate_candidate(candidate, icp)
                if not eligibility.eligible:
                    if eligibility.account_type == AccountType.PROFESSIONAL_SERVICES:
                        self._metrics["professional_services_excluded"] += 1
                    else:
                        self._metrics["accounts_without_owner_evidence"] += 1
                    self._metrics["account_type_exclusions"].append(
                        f"{candidate.name}: {eligibility.reason}"
                    )
                    continue
                try:
                    companies.append(
                        self._upsert_company(candidate.model_copy(update={"account_type": eligibility.account_type}))
                    )
                except Exception as exc:
                    self._record_error(run_id, f"Company persistence failed: {exc}")
            self._metrics["accounts_discovered"] = len(companies)
            self._metrics["official_site_accounts"] = sum(bool(item.candidate.website_url) for item in companies)
            self._persist_metrics(run_id)

            await asyncio.gather(*(self._research_company(run_id, icp, company) for company in companies))
            if self._is_cancel_requested(run_id):
                status = "cancelled"
            else:
                self._refresh_quota_metrics(run_id)
                status = "completed_with_shortfall" if self._metrics["shortfall_reasons"] else "completed"
            self._update_run(run_id, status=status, finished_at=utc_now(), metrics=self._metrics, errors=self._errors)
            self._emit(
                "complete",
                f"Run {status}: {self._metrics['qualified_prospects']} qualified prospects "
                f"from {self._metrics['contacts_discovered']} discovered",
            )
            return RunResult(run_id=run_id, status=status, metrics=self._metrics, errors=self._errors)
        except asyncio.CancelledError:
            self._update_run(run_id, status="cancelled", finished_at=utc_now(), metrics=self._metrics, errors=self._errors)
            return RunResult(run_id=run_id, status="cancelled", metrics=self._metrics, errors=self._errors)
        except Exception as exc:
            self._record_error(run_id, f"Run failed: {exc}")
            self._update_run(run_id, status="failed", finished_at=utc_now(), metrics=self._metrics, errors=self._errors)
            return RunResult(run_id=run_id, status="failed", metrics=self._metrics, errors=self._errors)

    async def _call(self, operation_factory: Callable[[], Awaitable[Any]], run_id: str) -> Any:
        if self._is_cancel_requested(run_id):
            raise asyncio.CancelledError("Run cancellation requested")
        last_error: Exception | None = None
        for attempt in range(self.settings.agent_retries + 1):
            try:
                async with self._agent_semaphore:
                    result = await operation_factory()
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.settings.agent_retries:
                    raise
                self._metrics["retries"] += 1
                self._emit("retry", f"Browser task retry {attempt + 1}/{self.settings.agent_retries}")
        else:  # pragma: no cover - loop either breaks or raises
            raise last_error or RuntimeError("Browser task failed without an error")
        self._metrics["agent_steps"] += result.steps
        self._metrics["agent_seconds"] = round(self._metrics["agent_seconds"] + result.duration_seconds, 2)
        self._errors.extend(result.errors)
        if self._metrics["agent_steps"] > self.settings.model_step_budget:
            raise RuntimeError("Configured model step budget exceeded")
        return result

    async def _research_company(self, run_id: str, icp: ICPProfile, company: PersistedCompany) -> None:
        if self._is_cancel_requested(run_id):
            return
        self._emit("contacts", f"Finding contacts at {company.candidate.name}")
        try:
            contacts_result = await self._call(lambda: self.contact_agent.discover(icp, company.candidate), run_id)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._record_error(run_id, f"Contact discovery failed for {company.candidate.name}: {exc}")
            return

        persisted_contacts = [
            self._upsert_prospect(run_id, icp, company, contact)
            for contact in contacts_result.output.contacts[: self.settings.max_prospects_per_account]
        ]
        self._persist_committee(company, contacts_result.output.committee_members, persisted_contacts)
        self._metrics["contacts_discovered"] += len(persisted_contacts)
        self._persist_metrics(run_id)
        await asyncio.gather(*(self._research_rapport(run_id, icp, item) for item in persisted_contacts))

    async def _research_rapport(self, run_id: str, icp: ICPProfile, prospect: PersistedProspect) -> None:
        if self._is_cancel_requested(run_id):
            return
        self._emit("rapport", f"Finding public context for {prospect.full_name}")
        try:
            result = await self._call(
                lambda: self.rapport_agent.research(icp, prospect.company.candidate, prospect.full_name, prospect.role),
                run_id,
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._record_error(run_id, f"Rapport research failed for {prospect.full_name}: {exc}")
            return
        with session_scope(self.session_factory) as session:
            db_prospect = session.get(Prospect, prospect.id)
            db_company = session.get(Company, prospect.company.id)
            if not db_prospect or not db_company:
                return
            signals = [self._signal_to_dict(signal) for signal in result.output.rapport_signals]
            db_prospect.rapport_signals = self._merge_signal_dicts(db_prospect.rapport_signals, signals)
            for signal in result.output.rapport_signals:
                add_research_note(
                    session,
                    db_prospect,
                    category=signal.category,
                    content=signal.summary,
                    source_url=signal.source_url,
                    source_type=signal.source_type.value,
                )
            signal_claims = [
                self._signal_claim(signal.category, signal.summary, signal.source_url, signal.supporting_excerpt, signal.source_type)
                for signal in result.output.rapport_signals
            ]
            persist_claims(session, signal_claims, company=db_company, prospect=db_prospect)
            for signal in result.output.account_signals:
                self._persist_account_signal(session, db_company, signal)
            db_prospect.source_urls = merge_unique_urls(
                db_prospect.source_urls,
                result.source_urls,
                [signal.source_url for signal in result.output.rapport_signals],
            )
            self._recalculate_scores(session, run_id, db_prospect, db_company, icp)
        self._persist_metrics(run_id)

    def _create_run(self, icp: ICPProfile) -> str:
        with session_scope(self.session_factory) as session:
            run = ResearchRun(icp_json=icp.model_dump(mode="json"), status="queued")
            session.add(run)
            session.flush()
            return run.id

    def _upsert_company(self, candidate: CompanyCandidate) -> PersistedCompany:
        domain = normalize_domain(candidate.canonical_domain or candidate.website_url)
        with session_scope(self.session_factory) as session:
            company = session.scalar(select(Company).where(Company.canonical_domain == domain))
            if not company:
                company = Company(name=candidate.name, canonical_domain=domain)
                session.add(company)
            company.name = candidate.name or company.name
            company.website_url = candidate.website_url or company.website_url
            company.industry = candidate.industry or company.industry
            company.company_size_band = candidate.company_size_band or company.company_size_band
            company.geography = candidate.geography or company.geography
            existing_account_type = AccountType(company.account_type or AccountType.UNKNOWN.value)
            candidate_account_type = classify_candidate(candidate)
            resolved_account_type = (
                candidate_account_type
                if candidate_account_type != AccountType.UNKNOWN
                else existing_account_type
            )
            company.account_type = resolved_account_type.value
            company.source_urls = merge_unique_urls(company.source_urls, candidate.source_urls, [claim.source_url for claim in candidate.evidence])
            session.flush()
            persist_claims(session, candidate.evidence, company=company)
            return PersistedCompany(
                id=company.id,
                candidate=candidate.model_copy(
                    update={"canonical_domain": domain, "account_type": resolved_account_type}
                ),
            )

    def _upsert_prospect(
        self,
        run_id: str,
        icp: ICPProfile,
        company: PersistedCompany,
        candidate: ContactCandidate,
    ) -> PersistedProspect:
        with session_scope(self.session_factory) as session:
            db_company = session.get(Company, company.id)
            if not db_company:
                raise ValueError("Company disappeared before contact persistence")
            identity_key = normalize_text(candidate.full_name)
            prospect = session.scalar(
                select(Prospect).where(Prospect.company_id == company.id, Prospect.identity_key == identity_key)
            )
            if not prospect:
                prospect = Prospect(
                    company=db_company,
                    full_name=candidate.full_name,
                    identity_key=identity_key,
                    role=candidate.role,
                    normalized_role=normalize_text(candidate.role),
                )
                session.add(prospect)
            prospect.full_name = candidate.full_name
            prospect.role = candidate.role
            prospect.normalized_role = normalize_text(candidate.role)
            prospect.email = candidate.email or prospect.email
            prospect.phone = candidate.phone or prospect.phone
            prospect.profile_url = candidate.profile_url or prospect.profile_url
            prospect.source_urls = merge_unique_urls(prospect.source_urls, candidate.source_urls, [claim.source_url for claim in candidate.evidence])
            session.flush()
            persist_claims(session, candidate.evidence, company=db_company, prospect=prospect)
            self._recalculate_scores(session, run_id, prospect, db_company, icp)
            if candidate.committee_role.value != "unknown":
                self._upsert_committee_member(
                    session,
                    db_company,
                    prospect,
                    candidate.full_name,
                    candidate.role,
                    candidate.committee_role.value,
                    candidate.source_urls,
                )
            return PersistedProspect(id=prospect.id, full_name=prospect.full_name, role=prospect.role, company=company)

    def _recalculate_scores(self, session: Session, run_id: str, prospect: Prospect, company: Company, icp: ICPProfile) -> None:
        evidence = list(prospect.evidence_records)
        confidence_data = {
            "full_name": prospect.full_name,
            "role": prospect.role,
            "company_domain": company.canonical_domain,
            "email": prospect.email,
            "phone": prospect.phone,
            "profile_url": prospect.profile_url,
            "rapport_signals": prospect.rapport_signals,
            "evidence": [
                {"field_name": item.field_name, "source_url": item.source_url, "source_type": item.source_type}
                for item in evidence
            ],
        }
        prospect.confidence_score = calculate_confidence_score(confidence_data, prospect.source_urls)
        prospect.score_version = SCORE_VERSION
        account_signals = list(company.account_signals)
        alignment = calculate_icp_alignment(prospect, company, icp, account_signals)
        bucket = "target" if any(reason.startswith("target role") for reason in alignment.reasons) else "adjacent"
        adjustment, adjustment_reason = feedback_adjustment(session, bucket, self.settings.feedback_minimum_reviews)
        link = session.scalar(
            select(ResearchRunProspect).where(
                ResearchRunProspect.research_run_id == run_id,
                ResearchRunProspect.prospect_id == prospect.id,
            )
        )
        if not link:
            link = ResearchRunProspect(research_run_id=run_id, prospect=prospect)
            session.add(link)
        link.icp_alignment_score = round(min(1.0, max(0.0, alignment.score + adjustment)), 3)
        link.feedback_adjustment = adjustment
        link.alignment_reasons = [*alignment.reasons, adjustment_reason]
        link.is_suppressed = is_suppressed(session, prospect)
        if link.is_suppressed:
            self._metrics["suppressed"] += 1
        if prospect.confidence_score > 0.85:
            self._metrics["corroborated_prospects"] += 1

    def _persist_committee(
        self,
        company: PersistedCompany,
        candidates: list[CommitteeCandidate],
        contacts: list[PersistedProspect],
    ) -> None:
        by_name = {normalize_text(item.full_name): item for item in contacts}
        with session_scope(self.session_factory) as session:
            db_company = session.get(Company, company.id)
            if not db_company:
                return
            for candidate in candidates:
                linked = by_name.get(normalize_text(candidate.full_name))
                self._upsert_committee_member(
                    session,
                    db_company,
                    session.get(Prospect, linked.id) if linked else None,
                    candidate.full_name,
                    candidate.role,
                    candidate.committee_role.value,
                    candidate.source_urls,
                )
                persist_claims(session, candidate.evidence, company=db_company, prospect=session.get(Prospect, linked.id) if linked else None)

    @staticmethod
    def _upsert_committee_member(
        session: Session,
        company: Company,
        prospect: Prospect | None,
        full_name: str,
        role: str,
        committee_role: str,
        source_urls: list[str],
    ) -> BuyingCommitteeMember:
        name_key = normalize_text(full_name)
        member = session.scalar(
            select(BuyingCommitteeMember).where(
                BuyingCommitteeMember.company_id == company.id,
                BuyingCommitteeMember.name_key == name_key,
                BuyingCommitteeMember.committee_role == committee_role,
            )
        )
        if not member:
            member = BuyingCommitteeMember(
                company=company,
                prospect_id=prospect.id if prospect else None,
                full_name=full_name,
                name_key=name_key,
                role=role,
                committee_role=committee_role,
            )
            session.add(member)
        member.role = role
        member.confidence_score = prospect.confidence_score if prospect else member.confidence_score
        member.source_urls = merge_unique_urls(member.source_urls, source_urls)
        session.flush()
        return member

    @staticmethod
    def _signal_to_dict(signal: RapportSignalCandidate) -> dict[str, str]:
        return {
            "category": signal.category,
            "summary": signal.summary,
            "source_url": signal.source_url,
            "source_type": signal.source_type.value,
        }

    @staticmethod
    def _merge_signal_dicts(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for signal in [*existing, *incoming]:
            key = (str(signal.get("summary", "")), str(signal.get("source_url", "")))
            if key not in seen:
                seen.add(key)
                merged.append(signal)
        return merged

    @staticmethod
    def _signal_claim(field_name: str, value: str, source_url: str, excerpt: str, source_type: Any):
        from .schemas import EvidenceClaim

        return EvidenceClaim(
            field_name=field_name,
            value=value,
            source_url=source_url,
            supporting_excerpt=excerpt,
            source_type=source_type,
        )

    @staticmethod
    def _persist_account_signal(session: Session, company: Company, signal: AccountSignalCandidate) -> None:
        existing = session.scalar(
            select(AccountSignal).where(
                AccountSignal.company_id == company.id,
                AccountSignal.kind == signal.kind,
                AccountSignal.source_url == signal.source_url,
            )
        )
        if existing:
            return
        session.add(
            AccountSignal(
                company=company,
                kind=signal.kind,
                description=signal.description,
                source_url=signal.source_url,
                source_type=classify_source(signal.source_url, company.canonical_domain, signal.source_type).value,
            )
        )

    def _is_cancel_requested(self, run_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            run = session.get(ResearchRun, run_id)
            return bool(run and run.cancel_requested)

    def _refresh_quota_metrics(self, run_id: str) -> None:
        """Compute outcome coverage after all persisted evidence has updated alignment."""
        with session_scope(self.session_factory) as session:
            links = list(
                session.scalars(
                    select(ResearchRunProspect).where(ResearchRunProspect.research_run_id == run_id)
                )
            )
            qualified = [
                link
                for link in links
                if not link.is_suppressed
                and link.icp_alignment_score >= self.settings.qualified_prospect_alignment_threshold
            ]
        self._metrics["qualified_prospects"] = len(qualified)
        self._metrics["unqualified_prospects"] = max(0, self._metrics["contacts_discovered"] - len(qualified))
        shortfalls: list[str] = []
        if self._metrics["accounts_discovered"] < self.settings.max_accounts_per_run:
            shortfalls.append(
                "Account target shortfall: "
                f"found {self._metrics['accounts_discovered']} of {self.settings.max_accounts_per_run} accounts."
            )
        if len(qualified) < self.settings.target_qualified_prospects_per_run:
            shortfalls.append(
                "Qualified prospect target shortfall: "
                f"found {len(qualified)} of {self.settings.target_qualified_prospects_per_run} at ICP alignment "
                f">= {self.settings.qualified_prospect_alignment_threshold:.2f}."
            )
        self._metrics["shortfall_reasons"] = shortfalls

    def _update_run(self, run_id: str, **values: Any) -> None:
        with session_scope(self.session_factory) as session:
            run = session.get(ResearchRun, run_id)
            if run:
                for key, value in values.items():
                    setattr(run, key, value)

    def _persist_metrics(self, run_id: str) -> None:
        self._update_run(run_id, metrics=dict(self._metrics), errors=list(self._errors))

    def _record_error(self, run_id: str, message: str) -> None:
        self._errors.append(message)
        self._persist_metrics(run_id)

    def _emit(self, stage: str, message: str) -> None:
        if self._progress:
            self._progress(stage, message)
