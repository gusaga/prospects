from __future__ import annotations

import pytest

from prospecting.schemas import ICPProfile


def test_icp_serializes_canonical_json_and_deduplicates_values():
    icp = ICPProfile(
        industry="B2B SaaS",
        company_size_band="51–200",
        geography="United States",
        pain_points=["Manual reporting", "manual reporting"],
        target_job_titles=["VP Sales", "VP Sales"],
        adjacent_personas=["Revenue Operations", "Revenue Operations"],
    )
    assert icp.pain_points == ["Manual reporting"]
    assert icp.model_dump(mode="json")["target_job_titles"] == ["VP Sales"]


def test_icp_rejects_empty_persona_lists():
    with pytest.raises(ValueError):
        ICPProfile(
            industry="B2B SaaS",
            company_size_band="51–200",
            geography="United States",
            pain_points=["x"],
            target_job_titles=["VP Sales"],
            adjacent_personas=["  "],
        )

