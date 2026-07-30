from __future__ import annotations

from prospecting.scoring import calculate_confidence_score, calculate_icp_alignment, explain_confidence_score
from prospecting.schemas import ICPProfile


def high_quality_data():
    return {
        "full_name": "Jane Doe",
        "role": "VP Sales",
        "company_domain": "acme.com",
        "email": "jane@acme.com",
        "rapport_signals": [{"summary": "Board member"}],
        "evidence": [
            {"field_name": "full_name", "source_url": "https://acme.com/team/jane", "source_type": "official"},
            {"field_name": "role", "source_url": "https://acme.com/team/jane", "source_type": "official"},
            {"field_name": "company", "source_url": "https://example.edu/board/jane", "source_type": "gov_edu"},
        ],
    }


def test_high_confidence_requires_official_and_independent_corroboration():
    data = high_quality_data()
    score = calculate_confidence_score(data, ["https://acme.com/team/jane", "https://example.edu/board/jane"])
    assert score > 0.85
    explanation = explain_confidence_score(data, ["https://acme.com/team/jane"])
    assert explanation.score <= 0.85
    assert explanation.cap_applied


def test_alignment_includes_role_company_and_signal_match():
    icp = ICPProfile(
        industry="B2B SaaS",
        company_size_band="51–200",
        geography="United States",
        pain_points=["manual reporting"],
        target_job_titles=["VP Sales"],
        adjacent_personas=["Sales Operations"],
    )
    result = calculate_icp_alignment(
        {"role": "VP Sales"},
        {"industry": "B2B SaaS", "company_size_band": "51–200", "geography": "United States"},
        icp,
        [{"kind": "Hiring", "description": "Hiring to improve manual reporting"}],
    )
    assert 0.9 <= result.score <= 1.0
    assert "target role matches ICP" in result.reasons


def test_alignment_handles_title_variants_and_multi_value_icp_fields():
    icp = ICPProfile(
        industry="Land Development, Project Management Tech SaaS",
        company_size_band="11–50",
        geography="United States, Texas, Arizona, Florida",
        pain_points=["multiple tools"],
        target_job_titles=["VP of Land Development"],
        adjacent_personas=["VP of Acquisitions"],
    )
    result = calculate_icp_alignment(
        {"role": "Vice President of Land Development"},
        {"industry": "Residential land development", "geography": "Arizona"},
        icp,
    )
    assert result.score >= 0.7
    assert "target role matches ICP" in result.reasons
    assert "industry matches ICP" in result.reasons
    assert "geography matches ICP" in result.reasons


def test_alignment_accepts_published_seniority_and_market_qualifiers_for_the_same_persona():
    icp = ICPProfile(
        industry="Land Development",
        company_size_band="11â€“50",
        geography="Texas",
        pain_points=["multiple tools"],
        target_job_titles=["Division President"],
        adjacent_personas=["VP of Land Development"],
    )
    result = calculate_icp_alignment(
        {"role": "Senior Division President, Houston"},
        {"industry": "Residential land development", "geography": "Texas"},
        icp,
    )
    assert result.score >= 0.75
    assert "target role matches ICP" in result.reasons


def test_direct_owner_audience_counts_when_homebuilder_industry_label_is_more_specific():
    icp = ICPProfile(
        industry="Land Development, Project Management Tech SaaS",
        company_size_band="11–50",
        geography="Texas",
        pain_points=["multiple tools"],
        target_job_titles=["VP of Land Development"],
        adjacent_personas=["Division President"],
        target_account_type="owner_developer",
    )
    result = calculate_icp_alignment(
        {"role": "Houston Division President"},
        {
            "industry": "Houston-area homebuilder",
            "geography": "Texas",
            "account_type": "owner_developer",
        },
        icp,
    )
    assert result.score >= 0.55
    assert "adjacent persona matches ICP" in result.reasons
    assert "direct owner/developer account matches ICP audience" in result.reasons
