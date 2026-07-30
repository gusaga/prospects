"""Recheck expired public evidence and flag stale/changed claims for review."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import select

from prospecting.config import get_settings
from prospecting.database import build_session_factory, create_db_engine, initialize_database, session_scope
from prospecting.evidence import mark_expired_claims, refresh_evidence_record
from prospecting.models import EvidenceRecord


async def refresh_records(records: list[EvidenceRecord]) -> list[object]:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "EvidenceProspecting/0.1"}) as client:
        return await asyncio.gather(*(refresh_evidence_record(client, record) for record in records))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--days", type=int, default=None, help="Override STALE_AFTER_DAYS")
    args = parser.parse_args()
    settings = get_settings()
    engine = create_db_engine(settings)
    initialize_database(engine)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        expired = mark_expired_claims(session, args.days or settings.stale_after_days)
        records = list(
            session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.freshness_status == "stale")
                .order_by(EvidenceRecord.extracted_at)
                .limit(args.limit)
            )
        )
        # Keep objects usable after the first transaction while retaining a short, deterministic batch.
        for record in records:
            session.expunge(record)
    results = asyncio.run(refresh_records(records)) if records else []
    with session_scope(factory) as session:
        for result in results:
            record = session.get(EvidenceRecord, result.evidence_id)
            if record:
                record.freshness_status = result.status
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(f"Marked {expired} records stale; refreshed {len(results)} records: {counts}")


if __name__ == "__main__":
    main()

