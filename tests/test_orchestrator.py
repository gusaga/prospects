from __future__ import annotations

from prospecting.agents import AgentRun
from prospecting.database import session_scope
from prospecting.models import AccountSignal, EvidenceRecord, Prospect, ResearchRun, ResearchRunProspect
from prospecting.orchestrator import ProspectingOrchestrator
from prospecting.schemas import (
    AccountDiscoveryOutput,
    CompanyCandidate,
    ContactCandidate,
    ContactDiscoveryOutput,
    EvidenceClaim,
    ICPProfile,
    RapportResearchOutput,
    RapportSignalCandidate,
    SourceType,
    TargetAccountType,
)


class FakeRunner:
    async def run(self, task, output_model):
        if output_model is AccountDiscoveryOutput:
            return AgentRun(
                output=AccountDiscoveryOutput(
                    accounts=[
                        CompanyCandidate(
                            name="Acme",
                            website_url="https://acme.com",
                            canonical_domain="acme.com",
                            industry="B2B SaaS",
                            company_size_band="51–200",
                            geography="United States",
                            source_urls=["https://acme.com"],
                            evidence=[
                                EvidenceClaim(
                                    field_name="company",
                                    value="Acme",
                                    source_url="https://acme.com",
                                    supporting_excerpt="Acme is a B2B SaaS company.",
                                    source_type=SourceType.OFFICIAL,
                                )
                            ],
                        )
                    ]
                ),
                source_urls=["https://acme.com"],
                steps=2,
            )
        if output_model is ContactDiscoveryOutput:
            return AgentRun(
                output=ContactDiscoveryOutput(
                    contacts=[
                        ContactCandidate(
                            full_name="Jane Doe",
                            role="VP Sales",
                            email="jane@acme.com",
                            source_urls=["https://acme.com/team", "https://university.edu/board/jane"],
                            evidence=[
                                EvidenceClaim(field_name="full_name", value="Jane Doe", source_url="https://acme.com/team", supporting_excerpt="Jane Doe leads sales.", source_type=SourceType.OFFICIAL),
                                EvidenceClaim(field_name="role", value="VP Sales", source_url="https://acme.com/team", supporting_excerpt="Jane Doe is VP Sales.", source_type=SourceType.OFFICIAL),
                                EvidenceClaim(field_name="company", value="Acme", source_url="https://university.edu/board/jane", supporting_excerpt="Jane Doe of Acme.", source_type=SourceType.GOV_EDU),
                            ],
                        )
                    ]
                ),
                steps=3,
            )
        if output_model is RapportResearchOutput:
            return AgentRun(
                output=RapportResearchOutput(
                    rapport_signals=[
                        RapportSignalCandidate(
                            category="board",
                            summary="Serves on a university entrepreneurship board.",
                            source_url="https://university.edu/board/jane",
                            source_type=SourceType.GOV_EDU,
                            supporting_excerpt="Jane Doe is a board member.",
                        )
                    ],
                    account_signals=[],
                ),
                steps=2,
            )
        raise AssertionError(f"Unexpected output model: {output_model}")


async def test_orchestrator_persists_partial_evidence_and_metrics(session_factory, settings):
    icp = ICPProfile(
        industry="B2B SaaS",
        company_size_band="51–200",
        geography="United States",
        pain_points=["manual reporting"],
        target_job_titles=["VP Sales"],
        adjacent_personas=["Sales Operations"],
        target_account_type=TargetAccountType.ANY,
    )
    result = await ProspectingOrchestrator(session_factory, FakeRunner(), settings).run(icp)
    assert result.status == "completed_with_shortfall"
    assert result.metrics["accounts_discovered"] == 1
    assert result.metrics["contacts_discovered"] == 1
    assert result.metrics["qualified_prospects"] == 1
    assert result.metrics["shortfall_reasons"]
    with session_scope(session_factory) as session:
        prospect = session.scalar(__import__("sqlalchemy").select(Prospect))
        assert prospect is not None
        assert prospect.confidence_score > 0.85
        assert len(prospect.rapport_signals) == 1
        assert session.scalar(__import__("sqlalchemy").select(ResearchRunProspect)) is not None
        assert session.scalar(__import__("sqlalchemy").select(EvidenceRecord)) is not None
