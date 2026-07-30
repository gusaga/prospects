from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from prospecting.database import session_scope
from prospecting.exports import export_csv, import_prospects_csv
from prospecting.models import Company, OutreachActivity, Prospect, RelationshipNote
from prospecting.outreach import update_outreach
from prospecting.relationship import add_relationship_note, add_research_note
from prospecting.review import add_suppression, apply_review, feedback_adjustment, is_suppressed, record_feedback


def create_prospect(session):
    company = Company(name="Acme", canonical_domain="acme.com")
    prospect = Prospect(
        company=company,
        full_name="Jane Doe",
        identity_key="jane doe",
        role="VP Sales",
        normalized_role="vp sales",
        email="jane@acme.com",
        confidence_score=0.9,
    )
    session.add(prospect)
    session.flush()
    return prospect


def test_review_feedback_and_suppression(session_factory):
    with session_scope(session_factory) as session:
        prospect = create_prospect(session)
        apply_review(session, prospect, "approve", notes="verified")
        for _ in range(10):
            record_feedback(session, prospect, "good_fit", persona_bucket="target")
        adjustment, reason = feedback_adjustment(session, "target", 10)
        assert adjustment > 0
        assert "10" in reason
        add_suppression(session, "email", "jane@acme.com", "already known")
        assert is_suppressed(session, prospect)
        assert export_csv(session) == (
            b"company,company_domain,full_name,role,email,phone,profile_url,linkedin_url,owner,"
            b"outreach_stage,next_action_at,last_activity_at,confidence_score,icp_alignment_score,"
            b"source_urls,rapport_signals\r\n"
        )


def test_csv_import_deduplicates_company_and_person(session_factory):
    payload = b"company,company_domain,full_name,role,email,owner\nAcme,acme.com,Jane Doe,VP Sales,jane@acme.com,Ari\nAcme,acme.com,Jane Doe,VP Sales,jane@acme.com,Ari\n"
    with session_scope(session_factory) as session:
        result = import_prospects_csv(session, payload)
        assert result == {"created": 1, "duplicates": 1}


def test_approval_starts_local_outreach_and_saves_activity_history(session_factory):
    with session_scope(session_factory) as session:
        prospect = create_prospect(session)
        apply_review(session, prospect, "approve")
        assert prospect.outreach_stage == "find_on_linkedin"
        assert prospect.next_action_at is not None

        update_outreach(
            session,
            prospect,
            stage="message_sent",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            notes="Sent a personalized connection request.",
        )
        activities = list(session.query(OutreachActivity).filter_by(prospect_id=prospect.id))
        assert prospect.outreach_stage == "message_sent"
        assert prospect.linkedin_url == "https://www.linkedin.com/in/jane-doe"
        assert len(activities) == 1
        assert activities[0].notes == "Sent a personalized connection request."


def test_approved_prospect_can_render_company_after_the_query_session_closes(session_factory):
    with session_scope(session_factory) as session:
        prospect = create_prospect(session)
        apply_review(session, prospect, "approve")

    with session_scope(session_factory) as session:
        prospects = list(
            session.scalars(
                select(Prospect)
                .options(joinedload(Prospect.company))
                .where(Prospect.review_status == "approved")
            )
        )

    assert prospects[0].company.name == "Acme"


def test_relationship_notes_are_bucketed_and_research_notes_do_not_duplicate(session_factory):
    with session_scope(session_factory) as session:
        prospect = create_prospect(session)
        add_relationship_note(
            session,
            prospect,
            bucket="education",
            content="Public bio lists a civil-engineering degree.",
        )
        first = add_research_note(
            session,
            prospect,
            category="projects",
            content="Leads a new master-planned community launch.",
            source_url="https://acme.com/news/community",
            source_type="official",
        )
        duplicate = add_research_note(
            session,
            prospect,
            category="projects",
            content="Leads a new master-planned community launch.",
            source_url="https://acme.com/news/community",
            source_type="official",
        )
        assert first is not None
        assert duplicate is None
        notes = list(session.query(RelationshipNote).filter_by(prospect_id=prospect.id))
        assert {note.bucket for note in notes} == {"education", "projects"}
