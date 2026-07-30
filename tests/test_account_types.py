from __future__ import annotations

from prospecting.account_types import evaluate_candidate
from prospecting.schemas import CompanyCandidate, EvidenceClaim, ICPProfile, SourceType, TargetAccountType


def _icp() -> ICPProfile:
    return ICPProfile(
        industry="Land Development",
        company_size_band="11–50",
        geography="Texas",
        pain_points=["Multiple tools"],
        target_job_titles=["VP of Land Development"],
        adjacent_personas=["Division President"],
        target_account_type=TargetAccountType.OWNER_DEVELOPER,
    )


def test_direct_owner_run_rejects_engineering_provider_even_with_land_development_language():
    candidate = CompanyCandidate(
        name="Example Civil Engineering",
        website_url="https://example-engineering.test",
        canonical_domain="example-engineering.test",
        industry="Civil infrastructure and land development engineering services",
        evidence=[
            EvidenceClaim(
                field_name="industry",
                value="Civil engineering",
                source_url="https://example-engineering.test/about",
                supporting_excerpt="We provide civil engineering and land development consulting services.",
                source_type=SourceType.OFFICIAL,
            )
        ],
    )

    decision = evaluate_candidate(candidate, _icp())

    assert not decision.eligible
    assert decision.account_type.value == "professional_services"
    assert decision.reason == "professional-services provider rather than an owner/developer"


def test_direct_owner_run_accepts_developer_with_official_owner_evidence():
    candidate = CompanyCandidate(
        name="Example Development Company",
        website_url="https://example-development.test",
        canonical_domain="example-development.test",
        industry="Residential land development",
        evidence=[
            EvidenceClaim(
                field_name="industry",
                value="Residential land development",
                source_url="https://example-development.test/about",
                supporting_excerpt="We acquire, entitle, and develop master-planned communities in Texas.",
                source_type=SourceType.OFFICIAL,
            )
        ],
    )

    decision = evaluate_candidate(candidate, _icp())

    assert decision.eligible
    assert decision.account_type.value == "owner_developer"


def test_company_level_developer_evidence_beats_engineering_language_in_a_staff_bio():
    candidate = CompanyCandidate(
        name="Example Master-Planned Communities",
        website_url="https://example-communities.test",
        canonical_domain="example-communities.test",
        industry="Master-planned community development",
        evidence=[
            EvidenceClaim(
                field_name="company_name",
                value="Example Master-Planned Communities",
                source_url="https://example-communities.test/about",
                supporting_excerpt=(
                    "Example is the developer of master-planned communities throughout Texas."
                ),
                source_type=SourceType.OFFICIAL,
            ),
            EvidenceClaim(
                field_name="company_association",
                value="VP of Land Development",
                source_url="https://example-communities.test/team",
                supporting_excerpt=(
                    "The Vice President leads engineering services, surveying, and technical execution."
                ),
                source_type=SourceType.OFFICIAL,
            ),
        ],
    )

    decision = evaluate_candidate(candidate, _icp())

    assert decision.eligible
    assert decision.account_type.value == "owner_developer"
