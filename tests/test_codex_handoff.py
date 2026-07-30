from __future__ import annotations

import json

from prospecting.codex_handoff import (
    handoff_prompt,
    ingest_codex_results,
    job_path,
    queue_gap_fill_run,
    queue_codex_run,
    result_path,
    validate_result_file,
)
from prospecting.database import session_scope
from prospecting.models import Prospect, ResearchRun
from prospecting.schemas import ICPProfile, TargetAccountType


def test_queued_codex_results_validate_and_ingest(session_factory, settings, tmp_path, monkeypatch):
    monkeypatch.setattr("prospecting.codex_handoff.HANDOFF_DIR", tmp_path)
    icp = ICPProfile(
        industry="B2B SaaS",
        company_size_band="51–200",
        geography="United States",
        pain_points=["manual reporting"],
        target_job_titles=["VP Sales"],
        adjacent_personas=["Sales Operations"],
        target_account_type=TargetAccountType.ANY,
    )
    run_id = queue_codex_run(
        session_factory,
        icp,
        settings.model_copy(update={"max_accounts_per_run": 1, "target_qualified_prospects_per_run": 1}),
    )
    payload = {
        "run_id": run_id,
        "accounts": [
            {
                "company": {
                    "name": "Acme",
                    "website_url": "https://acme.com",
                    "canonical_domain": "acme.com",
                    "industry": "B2B SaaS",
                    "company_size_band": "51–200",
                    "geography": "United States",
                    "source_urls": ["https://acme.com/about"],
                    "evidence": [
                        {
                            "field_name": "company",
                            "value": "Acme",
                            "source_url": "https://acme.com/about",
                            "supporting_excerpt": "Acme is a B2B SaaS company.",
                            "source_type": "official",
                        }
                    ],
                },
                "contacts": [
                    {
                        "contact": {
                            "full_name": "Jane Doe",
                            "role": "VP Sales",
                            "email": "jane@acme.com",
                            "source_urls": ["https://acme.com/team", "https://university.edu/board/jane"],
                            "evidence": [
                                {"field_name": "full_name", "value": "Jane Doe", "source_url": "https://acme.com/team", "supporting_excerpt": "Jane Doe leads sales.", "source_type": "official"},
                                {"field_name": "role", "value": "VP Sales", "source_url": "https://acme.com/team", "supporting_excerpt": "Jane Doe is VP Sales.", "source_type": "official"},
                                {"field_name": "company", "value": "Acme", "source_url": "https://university.edu/board/jane", "supporting_excerpt": "Jane Doe of Acme.", "source_type": "gov_edu"},
                            ],
                        },
                        "rapport": {
                            "rapport_signals": [
                                {"category": "board", "summary": "Serves on a university entrepreneurship board.", "source_url": "https://university.edu/board/jane", "source_type": "gov_edu", "supporting_excerpt": "Jane Doe is a board member."}
                            ],
                            "account_signals": [],
                        },
                    }
                ],
                "committee_members": [],
            }
        ],
    }
    result_path(run_id).write_text(json.dumps(payload), encoding="utf-8")
    assert validate_result_file(run_id, result_path(run_id)).run_id == run_id
    result = ingest_codex_results(session_factory, settings, run_id)
    assert result.status == "completed"
    assert handoff_prompt(run_id).startswith("Use $prospecting-codex-worker")
    job = json.loads(job_path(run_id).read_text(encoding="utf-8"))
    assert job["target_qualified_prospects"] == 1
    assert "delivery target" in " ".join(job["instructions"])
    with session_scope(session_factory) as session:
        assert session.get(ResearchRun, run_id).status == "completed"
        prospect = session.scalar(__import__("sqlalchemy").select(Prospect))
        assert prospect is not None
        assert prospect.confidence_score > 0.85
    top_up_id = queue_gap_fill_run(session_factory, settings, run_id)
    top_up_job = json.loads(job_path(top_up_id).read_text(encoding="utf-8"))
    assert top_up_job["continuation_of"] == run_id
    assert top_up_job["excluded_domains"] == ["acme.com"]
