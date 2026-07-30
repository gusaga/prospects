"""Pydantic contracts shared by the UI, agents, and orchestration layer."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceType(StrEnum):
    OFFICIAL = "official"
    GOV_EDU = "gov_edu"
    NONPROFIT = "nonprofit"
    PRESS_RELEASE = "press_release"
    REPUTABLE_MEDIA = "reputable_media"
    DIRECTORY = "directory"
    UNKNOWN = "unknown"


class CommitteeRole(StrEnum):
    CHAMPION = "champion"
    ECONOMIC_BUYER = "economic_buyer"
    TECHNICAL_EVALUATOR = "technical_evaluator"
    INFLUENCER = "influencer"
    BLOCKER = "blocker"
    UNKNOWN = "unknown"


class TargetAccountType(StrEnum):
    """Audience constraint applied to a prospecting run."""

    ANY = "any"
    OWNER_DEVELOPER = "owner_developer"


class AccountType(StrEnum):
    """Evidence-backed classification of the company, not of an individual contact."""

    OWNER_DEVELOPER = "owner_developer"
    PROFESSIONAL_SERVICES = "professional_services"
    OTHER = "other"
    UNKNOWN = "unknown"


class ICPProfile(BaseModel):
    """Canonical intake payload supplied to every research agent."""

    industry: str = Field(min_length=2, max_length=160)
    company_size_band: str = Field(min_length=2, max_length=80)
    geography: str = Field(default="Any", min_length=2, max_length=160)
    pain_points: list[str] = Field(min_length=1, max_length=12)
    target_job_titles: list[str] = Field(min_length=1, max_length=20)
    adjacent_personas: list[str] = Field(min_length=1, max_length=20)
    target_account_type: TargetAccountType = TargetAccountType.OWNER_DEVELOPER
    notes: str | None = Field(default=None, max_length=1_000)

    @field_validator("pain_points", "target_job_titles", "adjacent_personas")
    @classmethod
    def deduplicate_values(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            if value and value.casefold() not in seen:
                cleaned.append(value)
                seen.add(value.casefold())
        if not cleaned:
            raise ValueError("At least one non-empty value is required")
        return cleaned


class EvidenceClaim(BaseModel):
    """A single, inspectable claim returned by a browser agent."""

    field_name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=1_000)
    source_url: str = Field(min_length=8, max_length=2_048)
    supporting_excerpt: str = Field(min_length=1, max_length=2_000)
    source_type: SourceType = SourceType.UNKNOWN
    extracted_at: datetime = Field(default_factory=utc_now)


class CompanyCandidate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    website_url: str = Field(min_length=8, max_length=2_048)
    canonical_domain: str = Field(min_length=3, max_length=255)
    industry: str | None = Field(default=None, max_length=160)
    company_size_band: str | None = Field(default=None, max_length=80)
    geography: str | None = Field(default=None, max_length=160)
    account_type: AccountType = AccountType.UNKNOWN
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[EvidenceClaim] = Field(default_factory=list)


class AccountDiscoveryOutput(BaseModel):
    accounts: list[CompanyCandidate] = Field(default_factory=list)


class ContactCandidate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    role: str = Field(min_length=2, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    profile_url: str | None = Field(default=None, max_length=2_048)
    committee_role: CommitteeRole = CommitteeRole.UNKNOWN
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[EvidenceClaim] = Field(default_factory=list)


class CommitteeCandidate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    role: str = Field(min_length=2, max_length=255)
    committee_role: CommitteeRole = CommitteeRole.UNKNOWN
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[EvidenceClaim] = Field(default_factory=list)


class ContactDiscoveryOutput(BaseModel):
    contacts: list[ContactCandidate] = Field(default_factory=list)
    committee_members: list[CommitteeCandidate] = Field(default_factory=list)


class RapportSignalCandidate(BaseModel):
    category: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=4, max_length=1_000)
    source_url: str = Field(min_length=8, max_length=2_048)
    source_type: SourceType = SourceType.UNKNOWN
    supporting_excerpt: str = Field(min_length=1, max_length=2_000)


class AccountSignalCandidate(BaseModel):
    kind: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=4, max_length=1_000)
    source_url: str = Field(min_length=8, max_length=2_048)
    source_type: SourceType = SourceType.UNKNOWN
    supporting_excerpt: str = Field(min_length=1, max_length=2_000)


class RapportResearchOutput(BaseModel):
    rapport_signals: list[RapportSignalCandidate] = Field(default_factory=list, max_length=3)
    account_signals: list[AccountSignalCandidate] = Field(default_factory=list, max_length=5)


class CodexContactResearch(BaseModel):
    """One contact plus the public research gathered for them by a Codex worker."""

    contact: ContactCandidate
    rapport: RapportResearchOutput = Field(default_factory=RapportResearchOutput)


class CodexAccountResearch(BaseModel):
    """One complete account result in the no-API Codex handoff format."""

    company: CompanyCandidate
    contacts: list[CodexContactResearch] = Field(default_factory=list, max_length=20)
    committee_members: list[CommitteeCandidate] = Field(default_factory=list, max_length=30)


class CodexResearchResults(BaseModel):
    """Validated result file written by a human-triggered Codex research agent."""

    run_id: str = Field(min_length=1, max_length=64)
    accounts: list[CodexAccountResearch] = Field(default_factory=list, max_length=100)
    accounts_considered: int = Field(default=0, ge=0, le=10_000)
    search_queries: list[str] = Field(default_factory=list, max_length=100)
    shortfall_reasons: list[str] = Field(default_factory=list, max_length=30)


class RunResult(BaseModel):
    run_id: str
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
