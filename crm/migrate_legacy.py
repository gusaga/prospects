"""One-time migration from the legacy database (data/prospects.db) into the
new CRM schema (data/crm.db). Reads the old file with plain sqlite3 and never
writes to it."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .dedupe import name_key
from .models import Activity, Company, Prospect, Setting

LEGACY_DB = Path(__file__).resolve().parents[1] / "data" / "prospects.db"

# review_status had the final say in the old app; outreach_stage refined it.
STATUS_MAP = {
    "find_on_linkedin": "queued",
    "linkedin_found": "queued",
    "message_drafted": "queued",
    "message_sent": "follow_up",
    "replied": "follow_up",
    "demo_booked": "meeting",
    "nurture": "follow_up",
    "closed_lost": "not_fit",
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _map_status(review_status: str, outreach_stage: str | None) -> str:
    if review_status == "rejected":
        return "not_fit"
    if review_status == "approved":
        return STATUS_MAP.get(outreach_stage or "", "queued")
    return "new"


def _evidence_links(row_urls: str | None, evidence_rows: list[sqlite3.Row]) -> list[dict]:
    links: list[dict] = []
    seen: set[str] = set()
    for ev in evidence_rows:
        url = (ev["source_url"] or "").strip()
        if url and url.rstrip("/") not in seen:
            seen.add(url.rstrip("/"))
            links.append({"url": url, "note": ev["field_name"]})
    for url in json.loads(row_urls or "[]"):
        if url and url.rstrip("/") not in seen:
            seen.add(url.rstrip("/"))
            links.append({"url": url, "note": None})
    return links[:12]


def migrate(session: Session, legacy_path: Path | None = None) -> dict[str, int]:
    """Copy companies, prospects, notes and history into the new schema.

    Idempotent-ish: refuses to run if the target already has prospects,
    so it can't double-import.
    """
    if session.query(Prospect).count():
        raise RuntimeError("Target database already has prospects; migration refused.")

    source = sqlite3.connect(legacy_path or LEGACY_DB)
    source.row_factory = sqlite3.Row
    counts = {"companies": 0, "prospects": 0, "activities": 0}

    company_ids: dict[int, int] = {}
    for row in source.execute("SELECT * FROM companies"):
        company = Company(
            name=row["name"],
            domain=row["canonical_domain"],
            website=row["website_url"],
            industry=row["industry"],
            size_band=row["company_size_band"],
            region=row["geography"],
            created_at=_parse_dt(row["created_at"]),
        )
        session.add(company)
        session.flush()
        company_ids[row["id"]] = company.id
        counts["companies"] += 1

    for row in source.execute("SELECT * FROM prospects"):
        evidence_rows = list(source.execute(
            "SELECT field_name, source_url FROM evidence_records WHERE prospect_id = ? ORDER BY extracted_at DESC",
            (row["id"],),
        ))
        reasons_row = source.execute(
            "SELECT alignment_reasons FROM research_run_prospects WHERE prospect_id = ? ORDER BY id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        reasons = json.loads(reasons_row["alignment_reasons"]) if reasons_row else []
        rationale = "; ".join(reasons)[:490] if reasons else None

        followup = _parse_dt(row["next_action_at"])
        prospect = Prospect(
            company_id=company_ids[row["company_id"]],
            full_name=row["full_name"],
            name_key=name_key(row["full_name"]),
            title=row["role"],
            phone=row["phone"],
            email=row["email"],
            linkedin_url=row["linkedin_url"] or row["profile_url"],
            region=None,  # inherited from company at display time
            icp_score=round((row["confidence_score"] or 0) * 100) or None,
            icp_rationale=rationale,
            evidence=_evidence_links(row["source_urls"], evidence_rows),
            status=_map_status(row["review_status"], row["outreach_stage"]),
            priority=2,
            notes=row["outreach_notes"],
            source="legacy",
            last_contacted_at=_parse_dt(row["last_activity_at"]),
            next_followup_on=followup.date() if followup else None,
            created_at=_parse_dt(row["created_at"]),
        )
        session.add(prospect)
        session.flush()
        counts["prospects"] += 1

        for note in source.execute(
            "SELECT bucket, content, source_url, created_at FROM relationship_notes WHERE prospect_id = ?",
            (row["id"],),
        ):
            body = f"[{note['bucket']}] {note['content']}"
            if note["source_url"]:
                body += f" ({note['source_url']})"
            session.add(Activity(
                prospect_id=prospect.id, kind="note", body=body,
                created_at=_parse_dt(note["created_at"]),
            ))
            counts["activities"] += 1

        for act in source.execute(
            "SELECT stage, notes, occurred_at FROM outreach_activities WHERE prospect_id = ? ORDER BY occurred_at",
            (row["id"],),
        ):
            body = f"Legacy outreach stage: {act['stage']}"
            if act["notes"]:
                body += f" — {act['notes']}"
            session.add(Activity(
                prospect_id=prospect.id, kind="status", body=body,
                created_at=_parse_dt(act["occurred_at"]),
            ))
            counts["activities"] += 1

        session.add(Activity(prospect_id=prospect.id, kind="system", body="Migrated from legacy database"))

    # Seed ICP settings from the newest legacy run, with the confirmed region.
    icp_row = source.execute(
        "SELECT icp_json FROM research_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    legacy_icp = json.loads(icp_row["icp_json"]) if icp_row else {}
    session.merge(Setting(key="icp", value={
        "product": "Project-management SaaS for land development teams",
        "industry": legacy_icp.get("industry", "Single-family residential land development / homebuilding"),
        "company_size": legacy_icp.get("company_size_band", "11-50"),
        "regions": ["Texas", "Arizona", "Florida", "Georgia", "North Carolina", "Tennessee"],
        "region_note": "Sun Belt broadly — TX/AZ/FL first, neighbors welcome",
        "target_titles": legacy_icp.get("target_job_titles", ["VP of Land Development"]),
        "adjacent_titles": legacy_icp.get(
            "adjacent_personas",
            ["Senior Land Development Manager", "Division President", "VP of Acquisitions"],
        ),
        "account_rule": (
            "Owner/developers, master developers, and homebuilders only. Exclude engineering, "
            "surveying, planning, architecture, consulting, and construction-management firms "
            "even if they employ matching titles."
        ),
        "pain_points": legacy_icp.get("pain_points", ["Multiple tools", "lack of centralized database"]),
        "notes": legacy_icp.get("notes", "Single-family residential, home builder and land development management."),
    }))

    source.close()
    return counts
