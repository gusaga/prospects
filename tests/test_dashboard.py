from __future__ import annotations

from prospecting.dashboard import build_dashboard_snapshot
from prospecting.database import session_scope
from prospecting.models import Company, EvidenceRecord, Prospect, ResearchRun, ResearchRunProspect


def test_dashboard_snapshot_projects_saved_accounts_contacts_and_run_alignment(session_factory):
    with session_scope(session_factory) as session:
        company = Company(
            name="Acme Development",
            canonical_domain="acme.test",
            website_url="https://acme.test",
            industry="Land Development",
            geography="Texas",
            account_type="owner_developer",
        )
        prospect = Prospect(
            company=company,
            full_name="Taylor Prospect",
            identity_key="taylor prospect",
            role="Vice President of Land Development",
            normalized_role="vice president land development",
            confidence_score=0.81,
            source_urls=["https://acme.test/team"],
            rapport_signals=[{"summary": "Leads development planning."}],
        )
        run = ResearchRun(status="completed", icp_json={"industry": "Land Development"}, metrics={"qualified_prospects": 1})
        session.add_all([company, prospect, run])
        session.flush()
        session.add(
            EvidenceRecord(
                company=company,
                prospect=prospect,
                field_name="role",
                value=prospect.role,
                supporting_excerpt="Taylor Prospect is Vice President of Land Development.",
                source_url="https://acme.test/team",
                registered_domain="acme.test",
                source_type="official",
                content_fingerprint="a" * 64,
            )
        )
        session.add(
            ResearchRunProspect(
                research_run=run,
                prospect=prospect,
                icp_alignment_score=0.75,
                alignment_reasons=["target role matches ICP"],
            )
        )

    with session_scope(session_factory) as session:
        snapshot = build_dashboard_snapshot(session)

    assert snapshot.totals == {
        "accounts": 1,
        "prospects": 1,
        "direct_accounts": 1,
        "direct_prospects": 1,
        "partner_accounts": 0,
        "partner_prospects": 0,
        "research_runs": 1,
        "reviewable": 1,
        "approved": 0,
        "needs_linkedin": 0,
        "active_outreach": 0,
        "demos_booked": 0,
        "published_contacts": 0,
        "evidence_records": 1,
    }
    assert snapshot.accounts[0]["contacts"] == 1
    assert snapshot.prospects[0]["best_alignment"] == 0.75
    assert snapshot.prospects[0]["alignment_by_run"] == {snapshot.runs[0]["id"]: 0.75}
