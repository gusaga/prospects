"""Minimal, privacy-conscious relationship context for local SDR work."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Prospect, RelationshipNote


RELATIONSHIP_NOTE_BUCKETS = (
    "professional",
    "education",
    "projects",
    "conversation",
    "research",
)

RELATIONSHIP_NOTE_LABELS = {
    "professional": "Professional context",
    "education": "Education & affiliations",
    "projects": "Projects & public news",
    "conversation": "Conversation note",
    "research": "Research note",
}


def relationship_note_label(bucket: str | None) -> str:
    return RELATIONSHIP_NOTE_LABELS.get(bucket or "research", "Research note")


def normalize_relationship_bucket(value: str | None) -> str:
    normalized = (value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in RELATIONSHIP_NOTE_BUCKETS:
        return normalized
    if normalized in {"school", "university", "affiliation", "affiliations"}:
        return "education"
    if normalized in {"project", "news", "public_news", "development"}:
        return "projects"
    if normalized in {"professional_life", "work", "career"}:
        return "professional"
    return "research"


def add_relationship_note(
    session: Session,
    prospect: Prospect,
    *,
    bucket: str,
    content: str,
    source_url: str | None = None,
    source_type: str = "manual",
) -> RelationshipNote:
    """Append a local, professional relationship note after minimal validation."""
    clean_content = content.strip()
    if not clean_content:
        raise ValueError("Relationship note content is required")
    note = RelationshipNote(
        prospect=prospect,
        bucket=normalize_relationship_bucket(bucket),
        content=clean_content,
        source_url=source_url.strip() if source_url else None,
        source_type=source_type,
    )
    session.add(note)
    session.flush()
    return note


def add_research_note(
    session: Session,
    prospect: Prospect,
    *,
    category: str,
    content: str,
    source_url: str,
    source_type: str,
) -> RelationshipNote | None:
    """Persist a cited public signal once, so repeated research does not duplicate a dossier."""
    bucket = normalize_relationship_bucket(category)
    existing = session.scalar(
        select(RelationshipNote).where(
            RelationshipNote.prospect_id == prospect.id,
            RelationshipNote.bucket == bucket,
            RelationshipNote.content == content.strip(),
            RelationshipNote.source_url == source_url,
        )
    )
    if existing:
        return None
    return add_relationship_note(
        session,
        prospect,
        bucket=bucket,
        content=content,
        source_url=source_url,
        source_type=source_type,
    )
