"""Reset prospect workflow so everyone looks newly found today.

Keeps companies, contact fields, evidence, enrichment, and activity history.
Only resets call-queue fields and the Added timestamp.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Activity, Prospect, utc_now


def reset_prospects_as_found_today(session: Session) -> dict[str, int]:
    """Set every prospect to status=new as if deposited today."""
    now = utc_now()
    prospects = list(session.scalars(select(Prospect)))
    by_status: dict[str, int] = {}
    for prospect in prospects:
        by_status[prospect.status] = by_status.get(prospect.status, 0) + 1
        prospect.status = "new"
        prospect.priority = 2
        prospect.last_contacted_at = None
        prospect.next_followup_on = None
        prospect.created_at = now
        prospect.updated_at = now
        session.add(
            Activity(
                prospect_id=prospect.id,
                kind="system",
                body="Reset to New — treated as found today (workflow refresh)",
            )
        )
    session.flush()
    return {
        "reset": len(prospects),
        "was_new": by_status.get("new", 0),
        "was_queued": by_status.get("queued", 0),
        "was_closed": by_status.get("not_fit", 0) + by_status.get("do_not_contact", 0),
        "new_count": session.scalar(
            select(func.count(Prospect.id)).where(Prospect.status == "new")
        )
        or 0,
    }
