"""Explainable confidence and ICP-alignment scoring."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .evidence import classify_source, normalize_domain, normalize_text
from .schemas import AccountType, ICPProfile, SourceType, TargetAccountType


SCORE_VERSION = "v3"
SOURCE_WEIGHTS: dict[SourceType, float] = {
    SourceType.OFFICIAL: 1.0,
    SourceType.GOV_EDU: 0.86,
    SourceType.NONPROFIT: 0.78,
    SourceType.PRESS_RELEASE: 0.74,
    SourceType.REPUTABLE_MEDIA: 0.68,
    SourceType.DIRECTORY: 0.36,
    SourceType.UNKNOWN: 0.25,
}


@dataclass(frozen=True)
class ConfidenceExplanation:
    score: float
    completeness: float
    source_quality: float
    corroboration: float
    official_source_present: bool
    independent_source_present: bool
    cap_applied: bool
    reasons: list[str]


def _get(data: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(data, Mapping):
        return data.get(key, default)
    return getattr(data, key, default)


def _has_value(data: Mapping[str, Any] | Any, *keys: str) -> bool:
    return any(bool(_get(data, key)) for key in keys)


def _source_info(prospect_data: Mapping[str, Any] | Any, source_urls: Sequence[str | Mapping[str, Any]]) -> list[tuple[str, SourceType]]:
    company_domain = _get(prospect_data, "company_domain") or _get(prospect_data, "canonical_domain")
    details: list[tuple[str, SourceType]] = []
    supplied_urls: set[str] = set()
    for item in source_urls:
        if isinstance(item, Mapping):
            url = str(item.get("source_url") or item.get("url") or "")
            declared = item.get("source_type")
        else:
            url = str(item)
            declared = None
        if not url:
            continue
        try:
            supplied_urls.add(url.rstrip("/"))
            kind = classify_source(url, company_domain, declared)
            details.append((normalize_domain(url), kind))
        except ValueError:
            continue
    for evidence in _get(prospect_data, "evidence", []) or []:
        if isinstance(evidence, Mapping):
            url = evidence.get("source_url")
            declared = evidence.get("source_type")
        else:
            url = getattr(evidence, "source_url", None)
            declared = getattr(evidence, "source_type", None)
        if url and str(url).rstrip("/") in supplied_urls:
            try:
                details.append((normalize_domain(str(url)), classify_source(str(url), company_domain, declared)))
            except ValueError:
                pass
    return details


def _key_fields_have_evidence(data: Mapping[str, Any] | Any) -> bool:
    evidence = _get(data, "evidence", []) or []
    names: set[str] = set()
    for item in evidence:
        field_name = item.get("field_name") if isinstance(item, Mapping) else getattr(item, "field_name", "")
        names.add(normalize_text(str(field_name)))
    has_identity = bool(names & {"full name", "full_name", "name"})
    has_role = bool(names & {"role", "job title", "title"})
    has_employment = bool(names & {"company", "employer", "company association", "company_name"})
    return sum((has_identity, has_role, has_employment)) >= 2


def explain_confidence_score(prospect_data: Mapping[str, Any] | Any, source_urls: Sequence[str | Mapping[str, Any]]) -> ConfidenceExplanation:
    """Return an inspectable confidence calculation in the range 0.0–1.0."""
    weights = (
        (0.20, _has_value(prospect_data, "full_name", "name"), "name"),
        (0.25, _has_value(prospect_data, "role", "job_title"), "role"),
        (0.25, _has_value(prospect_data, "company_domain", "canonical_domain", "company_name"), "company association"),
        (0.15, _has_value(prospect_data, "email", "phone", "profile_url"), "public work contact/profile"),
        (0.10, bool(_get(prospect_data, "rapport_signals", [])), "rapport signal"),
        (0.05, bool(_get(prospect_data, "evidence", [])), "field evidence"),
    )
    completeness_raw = sum(weight for weight, present, _ in weights if present)
    completeness = 0.40 * completeness_raw
    missing = [label for _, present, label in weights if not present]

    details = _source_info(prospect_data, source_urls)
    reliability = max((SOURCE_WEIGHTS[kind] for _, kind in details), default=0.0)
    source_quality = 0.35 * reliability
    official = any(kind == SourceType.OFFICIAL for _, kind in details)
    domains = {domain for domain, _ in details}
    independent = len(domains) >= 2
    key_fields = _key_fields_have_evidence(prospect_data)

    if official and independent and key_fields:
        corroboration = 0.25
    elif official and independent:
        corroboration = 0.18
    elif official:
        corroboration = 0.12
    elif independent:
        corroboration = 0.09
    else:
        corroboration = 0.0

    raw_score = completeness + source_quality + corroboration
    cap_applied = not (official and independent and key_fields)
    score = min(raw_score, 0.85) if cap_applied else min(raw_score, 1.0)
    reasons = [
        f"completeness={completeness_raw:.2f}",
        f"best_source_reliability={reliability:.2f}",
        f"independent_domains={len(domains)}",
    ]
    if missing:
        reasons.append("missing: " + ", ".join(missing))
    if cap_applied:
        reasons.append("capped at 0.85: official and independently corroborated identity/employment evidence is incomplete")
    return ConfidenceExplanation(
        score=round(score, 3),
        completeness=round(completeness, 3),
        source_quality=round(source_quality, 3),
        corroboration=round(corroboration, 3),
        official_source_present=official,
        independent_source_present=independent,
        cap_applied=cap_applied,
        reasons=reasons,
    )


def calculate_confidence_score(prospect_data: Mapping[str, Any] | Any, source_urls: Sequence[str | Mapping[str, Any]]) -> float:
    """Public scoring API requested by the product contract."""
    return explain_confidence_score(prospect_data, source_urls).score


@dataclass(frozen=True)
class AlignmentExplanation:
    score: float
    reasons: list[str]


_TITLE_STOP_WORDS = frozenset({"and", "at", "for", "in", "of", "the", "to"})


def _canonical_title(value: str) -> str:
    """Normalize common title variants without claiming semantic equivalence we cannot support."""
    normalized = normalize_text(value)
    normalized = re.sub(r"\b(evp)\b", "executive vice president", normalized)
    normalized = re.sub(r"\b(svp)\b", "senior vice president", normalized)
    normalized = re.sub(r"\b(vp)\b", "vice president", normalized)
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return " ".join(token for token in normalized.split() if token not in _TITLE_STOP_WORDS)


def _title_similarity(left: str, right: str) -> float:
    """Return a conservative token overlap for published job-title variations."""
    left_tokens = set(_canonical_title(left).split())
    right_tokens = set(_canonical_title(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    if left_tokens == right_tokens:
        return 1.0
    shared = left_tokens & right_tokens
    return len(shared) / max(len(left_tokens), len(right_tokens))


def _split_icp_terms(value: str) -> list[str]:
    return [term.strip() for term in re.split(r"[,;/\n]+", value) if term.strip()]


def _text_matches_any(candidate: str, configured_value: str) -> bool:
    """Match a documented company attribute against one configured ICP term."""
    normalized_candidate = normalize_text(candidate)
    if not normalized_candidate:
        return False
    for raw_term in _split_icp_terms(configured_value):
        term = normalize_text(raw_term)
        if not term or term in {"any", "all"}:
            continue
        if term in normalized_candidate or normalized_candidate in term:
            return True
        candidate_tokens = set(re.sub(r"[^\w\s]", " ", normalized_candidate).split())
        term_tokens = set(re.sub(r"[^\w\s]", " ", term).split())
        shared = candidate_tokens & term_tokens
        # One shared generic word (for example, "technology") is not enough.
        if len(shared) >= 2 and len(shared) / min(len(candidate_tokens), len(term_tokens)) >= 0.6:
            return True
    return False


def _role_match(role: str, titles: Sequence[str]) -> tuple[float, str | None]:
    role_tokens = set(_canonical_title(role).split())
    for title in titles:
        title_tokens = set(_canonical_title(title).split())
        # A published regional or senior qualifier should not invalidate the same role.
        # Examples: "Senior Division President" and "President, Texas Land Division"
        # are both clear variants of the configured "Division President" persona.
        if len(title_tokens) >= 2 and title_tokens.issubset(role_tokens):
            return 1.0, "exact"
        similarity = _title_similarity(role, title)
        if similarity == 1.0:
            return similarity, "exact"
        if similarity >= 0.75:
            return similarity, "close"
    return 0.0, None


def calculate_icp_alignment(
    prospect_data: Mapping[str, Any] | Any,
    company_data: Mapping[str, Any] | Any,
    icp: ICPProfile,
    account_signals: Sequence[Mapping[str, Any] | Any] = (),
) -> AlignmentExplanation:
    """Deterministically rank a prospect against the original ICP."""
    score = 0.0
    reasons: list[str] = []
    role = str(_get(prospect_data, "role", ""))
    _, target_match = _role_match(role, icp.target_job_titles)
    _, adjacent_match = _role_match(role, icp.adjacent_personas)
    if target_match == "exact":
        score += 0.45
        reasons.append("target role matches ICP")
    elif target_match == "close":
        score += 0.36
        reasons.append("target role closely matches ICP")
    elif adjacent_match == "exact":
        score += 0.27
        reasons.append("adjacent persona matches ICP")
    elif adjacent_match == "close":
        score += 0.21
        reasons.append("adjacent persona closely matches ICP")

    industry = normalize_text(str(_get(company_data, "industry", "")))
    if _text_matches_any(industry, icp.industry):
        score += 0.20
        reasons.append("industry matches ICP")
    elif (
        icp.target_account_type == TargetAccountType.OWNER_DEVELOPER
        and normalize_text(str(_get(company_data, "account_type", ""))) == AccountType.OWNER_DEVELOPER.value
    ):
        # In a direct-owner run, the audience constraint is stronger evidence of
        # market fit than an imprecise free-text industry label. Homebuilders often
        # describe themselves as builders rather than as "land development".
        score += 0.20
        reasons.append("direct owner/developer account matches ICP audience")
    size = normalize_text(str(_get(company_data, "company_size_band", "")))
    if _text_matches_any(size, icp.company_size_band):
        score += 0.10
        reasons.append("company size matches ICP")
    geography = normalize_text(str(_get(company_data, "geography", "")))
    if _text_matches_any(geography, icp.geography):
        score += 0.10
        reasons.append("geography matches ICP")

    signal_text = " ".join(
        normalize_text(str(_get(signal, "description", ""))) + " " + normalize_text(str(_get(signal, "kind", "")))
        for signal in account_signals
    )
    pain_matches = [pain for pain in icp.pain_points if _text_matches_any(signal_text, pain)]
    if pain_matches:
        score += min(0.15, 0.075 * len(pain_matches))
        reasons.append("public account signal matches ICP pain point")
    return AlignmentExplanation(score=round(min(score, 1.0), 3), reasons=reasons)
