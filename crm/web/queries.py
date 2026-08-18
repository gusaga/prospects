"""Prospect-list filtering shared by the list page, CSV export, and
enrichment-request creation."""

from __future__ import annotations

from datetime import date

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from ..models import Activity, Company, Prospect

SORTS = {
    "name": Prospect.full_name,
    "company": Company.name,
    "score": Prospect.icp_score,
    "priority": Prospect.priority,
    "status": Prospect.status,
    "followup": Prospect.next_followup_on,
    "contacted": Prospect.last_contacted_at,
    "added": Prospect.created_at,
}


def parse_filters(request: Request) -> dict:
    params = request.query_params
    return {
        "q": params.get("q", "").strip(),
        "status": params.get("status", "").strip(),
        "region": params.get("region", "").strip(),
        "priority": params.get("priority", "").strip(),
        "min_score": params.get("min_score", "").strip(),
        "due": params.get("due", "").strip(),
        "sort": params.get("sort", "added"),
        "dir": params.get("dir", "desc"),
    }


def prospect_query(session: Session, f: dict):
    stmt = select(Prospect).join(Prospect.company).options(joinedload(Prospect.company))
    if f["q"]:
        like = f"%{f['q']}%"
        stmt = stmt.where(or_(
            Prospect.full_name.ilike(like), Prospect.title.ilike(like),
            Prospect.email.ilike(like), Prospect.phone.ilike(like),
            Prospect.notes.ilike(like), Prospect.icp_rationale.ilike(like),
            Prospect.region.ilike(like), Prospect.city.ilike(like),
            Company.name.ilike(like), Company.domain.ilike(like),
            Company.industry.ilike(like), Company.region.ilike(like),
        ))
    if f["status"]:
        stmt = stmt.where(Prospect.status == f["status"])
    if f["region"]:
        like = f"%{f['region']}%"
        stmt = stmt.where(or_(Prospect.region.ilike(like), Company.region.ilike(like)))
    if f["priority"]:
        stmt = stmt.where(Prospect.priority == int(f["priority"]))
    if f["min_score"]:
        stmt = stmt.where(Prospect.icp_score >= int(f["min_score"]))
    if f["due"]:
        stmt = stmt.where(Prospect.next_followup_on.is_not(None),
                          Prospect.next_followup_on <= date.today())
    column = SORTS.get(f["sort"], Prospect.created_at)
    ordered = column.desc() if f["dir"] == "desc" else column.asc()
    # NULL scores/dates go last regardless of direction.
    stmt = stmt.order_by(column.is_(None), ordered, Prospect.id.desc())
    return stmt


def region_options(session: Session) -> list[str]:
    values: set[str] = set()
    for value in session.scalars(select(Company.region).distinct()):
        if value:
            values.add(value)
    for value in session.scalars(select(Prospect.region).distinct()):
        if value:
            values.add(value)
    return sorted(values)


def enriched_prospect_ids(session: Session, ids: list[int]) -> set[int]:
    """Prospects that received a Stage-2 deepen (activity logged by ingest)."""
    if not ids:
        return set()
    return set(
        session.scalars(
            select(Activity.prospect_id)
            .where(
                Activity.prospect_id.in_(ids),
                Activity.kind == "system",
                Activity.body.startswith("Enriched from import"),
            )
            .distinct()
        )
    )
