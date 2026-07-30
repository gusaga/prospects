"""Local, manual SDR outreach workflow helpers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .models import OutreachActivity, Prospect, utc_now


OUTREACH_STAGES = (
    "awaiting_review",
    "find_on_linkedin",
    "linkedin_found",
    "message_drafted",
    "message_sent",
    "replied",
    "demo_booked",
    "nurture",
    "closed_lost",
)

OUTREACH_STAGE_LABELS = {
    "awaiting_review": "Awaiting review",
    "find_on_linkedin": "Find on LinkedIn",
    "linkedin_found": "LinkedIn found",
    "message_drafted": "Message drafted",
    "message_sent": "Message sent",
    "replied": "Replied",
    "demo_booked": "Demo booked",
    "nurture": "Nurture",
    "closed_lost": "Closed lost",
}

TERMINAL_OUTREACH_STAGES = {"demo_booked", "closed_lost"}


def outreach_stage_label(stage: str | None) -> str:
    return OUTREACH_STAGE_LABELS.get(stage or "awaiting_review", "Awaiting review")


def update_outreach(
    session: Session,
    prospect: Prospect,
    *,
    stage: str,
    notes: str | None = None,
    linkedin_url: str | None = None,
    next_action_at: datetime | None = None,
    occurred_at: datetime | None = None,
) -> OutreachActivity:
    """Save a manual outreach update and append it to the prospect's local timeline."""
    if stage not in OUTREACH_STAGES:
        raise ValueError(f"Unsupported outreach stage: {stage}")
    if prospect.review_status != "approved":
        raise ValueError("Approve a prospect before adding outreach activity")

    occurred_at = occurred_at or utc_now()
    clean_notes = notes.strip() if notes else None
    clean_linkedin_url = linkedin_url.strip() if linkedin_url else None
    prospect.outreach_stage = stage
    if clean_linkedin_url:
        prospect.linkedin_url = clean_linkedin_url
    prospect.next_action_at = None if stage in TERMINAL_OUTREACH_STAGES else next_action_at
    prospect.last_activity_at = occurred_at
    if clean_notes:
        prospect.outreach_notes = clean_notes

    activity = OutreachActivity(
        prospect=prospect,
        stage=stage,
        notes=clean_notes,
        occurred_at=occurred_at,
        next_action_at=prospect.next_action_at,
    )
    session.add(activity)
    session.flush()
    return activity
