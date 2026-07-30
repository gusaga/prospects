"""Account-type classification and direct-owner eligibility rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .schemas import AccountType, CompanyCandidate, ICPProfile, TargetAccountType


_SERVICE_PROVIDER_PATTERN = re.compile(
    r"\b(professional services|(?:civil )?engineering (?:firm|services|design|consulting)|"
    r"surveying (?:firm|services)|architecture (?:firm|services)|consulting (?:firm|services)|"
    r"construction management)\b",
    re.IGNORECASE,
)
_OWNER_DEVELOPER_PATTERN = re.compile(
    r"\b(owner[\s-]*(?:and|&)?[\s-]*developer|real estate developer|real estate development|"
    r"development company|master developer|master[\s-]planned community development|homebuilder|"
    r"home builder|homebuilding|landowner|land owner|(?:is|the) developer of|"
    r"acquir(?:e|es|ed|ing)\s+(?:land|property|properties).{0,80}\bdevelop|"
    r"land banking|build[\s-]to[\s-]rent|btr communities)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AccountEligibility:
    """A deterministic decision suitable for metrics and audit trails."""

    eligible: bool
    account_type: AccountType
    reason: str | None = None


def account_type_label(value: AccountType | str | None) -> str:
    """Return a concise dashboard label for a persisted classification."""
    labels = {
        AccountType.OWNER_DEVELOPER.value: "Direct owner/developer",
        AccountType.PROFESSIONAL_SERVICES.value: "Potential partner/service firm",
        AccountType.OTHER.value: "Other account type",
        AccountType.UNKNOWN.value: "Needs account-type verification",
    }
    return labels.get(str(value or AccountType.UNKNOWN.value), labels[AccountType.UNKNOWN.value])


def _research_text(
    name: str,
    industry: str | None,
    evidence: Iterable[object] = (),
) -> str:
    parts = [name, industry or ""]
    for claim in evidence:
        parts.extend(
            [
                str(getattr(claim, "value", "")),
                str(getattr(claim, "supporting_excerpt", "")),
            ]
        )
    return " ".join(parts)


def infer_account_type(
    *,
    name: str,
    industry: str | None,
    evidence: Iterable[object] = (),
    declared: AccountType = AccountType.UNKNOWN,
) -> AccountType:
    """Classify an account from public, company-level evidence.

    An explicit company-level owner/developer declaration wins over a service term in a
    staff biography. Otherwise, narrowly scoped service-provider terms prevent an
    engineering firm from being treated as the developer's buyer.
    """
    text = _research_text(name, industry, evidence)
    if declared == AccountType.OWNER_DEVELOPER or _OWNER_DEVELOPER_PATTERN.search(text):
        return AccountType.OWNER_DEVELOPER
    if declared == AccountType.PROFESSIONAL_SERVICES or _SERVICE_PROVIDER_PATTERN.search(text):
        return AccountType.PROFESSIONAL_SERVICES
    if declared == AccountType.OTHER:
        return declared
    return AccountType.UNKNOWN


def classify_candidate(candidate: CompanyCandidate) -> AccountType:
    """Return the evidence-based account type for a discovery candidate."""
    return infer_account_type(
        name=candidate.name,
        industry=candidate.industry,
        evidence=candidate.evidence,
        declared=candidate.account_type,
    )


def evaluate_candidate(candidate: CompanyCandidate, icp: ICPProfile) -> AccountEligibility:
    """Enforce the ICP's account-audience rule before any contacts are researched."""
    account_type = classify_candidate(candidate)
    if icp.target_account_type == TargetAccountType.ANY:
        return AccountEligibility(True, account_type)
    if account_type == AccountType.OWNER_DEVELOPER:
        return AccountEligibility(True, account_type)
    if account_type == AccountType.PROFESSIONAL_SERVICES:
        return AccountEligibility(
            False,
            account_type,
            "professional-services provider rather than an owner/developer",
        )
    return AccountEligibility(
        False,
        account_type,
        "no official owner/developer evidence",
    )
