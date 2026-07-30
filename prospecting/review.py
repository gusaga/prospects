"""Human review, feedback, and suppression operations."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .evidence import normalize_text
from .models import Company, Prospect, ProspectFeedback, ResearchRun, ReviewDecision, SuppressionEntry, utc_now


VALID_ACTIONS = {"approve", "reject", "edit", "wrong_role", "stale"}
VALID_FEEDBACK = {"good_fit", "bad_fit", "wrong_role", "stale"}


def apply_review(
    session: Session,
    prospect: Prospect,
    action: str,
    *,
    research_run_id: str | None = None,
    notes: str | None = None,
    edits: dict[str, str | None] | None = None,
) -> ReviewDecision:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unsupported review action: {action}")
    if edits:
        editable = {"full_name", "role", "email", "phone", "profile_url", "owner"}
        for field, value in edits.items():
            if field not in editable:
                raise ValueError(f"Field is not editable: {field}")
            setattr(prospect, field, value)
        prospect.identity_key = normalize_text(prospect.full_name)
        prospect.normalized_role = normalize_text(prospect.role)
    if action == "approve":
        prospect.review_status = "approved"
        if prospect.outreach_stage in {None, "awaiting_review"}:
            prospect.outreach_stage = "find_on_linkedin"
            prospect.next_action_at = utc_now()
    elif action in {"reject", "wrong_role"}:
        prospect.review_status = "rejected"
        prospect.outreach_stage = "closed_lost"
        prospect.next_action_at = None
    elif action == "stale":
        prospect.review_status = "needs_review"
    decision = ReviewDecision(prospect=prospect, research_run_id=research_run_id, action=action, notes=notes)
    session.add(decision)
    session.flush()
    return decision


def record_feedback(
    session: Session,
    prospect: Prospect,
    label: str,
    *,
    research_run_id: str | None = None,
    persona_bucket: str = "other",
    notes: str | None = None,
) -> ProspectFeedback:
    if label not in VALID_FEEDBACK:
        raise ValueError(f"Unsupported feedback label: {label}")
    feedback = ProspectFeedback(
        prospect=prospect,
        research_run_id=research_run_id,
        label=label,
        persona_bucket=persona_bucket,
        notes=notes,
    )
    session.add(feedback)
    session.flush()
    return feedback


def add_suppression(session: Session, field_type: str, value: str, reason: str | None = None) -> SuppressionEntry:
    normalized = normalize_text(value)
    existing = session.scalar(
        select(SuppressionEntry).where(
            SuppressionEntry.field_type == field_type,
            SuppressionEntry.normalized_value == normalized,
        )
    )
    if existing:
        if reason:
            existing.reason = reason
        return existing
    entry = SuppressionEntry(field_type=field_type, normalized_value=normalized, reason=reason)
    session.add(entry)
    session.flush()
    return entry


def is_suppressed(session: Session, prospect: Prospect) -> bool:
    values = [
        ("email", prospect.email),
        ("name", prospect.full_name),
        ("domain", prospect.company.canonical_domain if prospect.company else None),
    ]
    for field_type, value in values:
        if not value:
            continue
        found = session.scalar(
            select(SuppressionEntry.id).where(
                SuppressionEntry.field_type == field_type,
                SuppressionEntry.normalized_value == normalize_text(value),
            )
        )
        if found:
            return True
    return False


def feedback_adjustment(session: Session, persona_bucket: str, minimum_reviews: int = 10) -> tuple[float, str]:
    """Return a bounded, transparent ranking adjustment; never alter confidence."""
    feedback = session.scalars(
        select(ProspectFeedback).where(ProspectFeedback.persona_bucket == persona_bucket)
    ).all()
    if len(feedback) < minimum_reviews:
        return 0.0, f"feedback not applied: {len(feedback)}/{minimum_reviews} reviewed {persona_bucket} prospects"
    positive = sum(item.label == "good_fit" for item in feedback)
    rate = (positive + 1) / (len(feedback) + 2)  # Laplace smoothing
    adjustment = max(-0.10, min(0.10, round((rate - 0.5) * 0.20, 3)))
    return adjustment, f"feedback adjustment {adjustment:+.3f} from {len(feedback)} {persona_bucket} reviews"


def list_review_queue(
    session: Session,
    run_id: str | None = None,
    *,
    account_type: str | None = None,
) -> list[Prospect]:
    statement = select(Prospect).where(Prospect.review_status.in_(("pending", "needs_review"))).order_by(Prospect.confidence_score.desc())
    if run_id:
        from .models import ResearchRunProspect

        statement = statement.join(ResearchRunProspect).where(ResearchRunProspect.research_run_id == run_id)
    if account_type:
        statement = statement.join(Company).where(Company.account_type == account_type)
    return list(session.scalars(statement).unique())


def request_run_cancel(session: Session, run_id: str) -> None:
    run = session.get(ResearchRun, run_id)
    if not run:
        raise ValueError(f"Unknown run: {run_id}")
    run.cancel_requested = True
