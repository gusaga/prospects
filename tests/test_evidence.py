from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from prospecting.database import session_scope
from prospecting.evidence import mark_expired_claims, persist_claims, refresh_evidence_record, utc_now
from prospecting.models import Company
from prospecting.schemas import EvidenceClaim, SourceType


def test_evidence_is_fingerprinted_deduplicated_and_marked_stale(session_factory):
    with session_scope(session_factory) as session:
        company = Company(name="Acme", canonical_domain="acme.com")
        session.add(company)
        session.flush()
        claim = EvidenceClaim(
            field_name="role",
            value="VP Sales",
            source_url="https://acme.com/team/jane",
            supporting_excerpt="Jane Doe is VP Sales.",
            source_type=SourceType.OFFICIAL,
            extracted_at=utc_now() - timedelta(days=40),
        )
        records = persist_claims(session, [claim, claim], company=company)
        assert len(records) == 2
        assert records[0].id == records[1].id
        assert len(records[0].content_fingerprint) == 64
        assert mark_expired_claims(session, 30) == 1
        assert records[0].freshness_status == "stale"


@pytest.mark.asyncio
async def test_refresh_marks_missing_claim_as_changed(session_factory):
    with session_scope(session_factory) as session:
        company = Company(name="Acme", canonical_domain="acme.com")
        session.add(company)
        session.flush()
        record = persist_claims(
            session,
            [
                EvidenceClaim(
                    field_name="role",
                    value="VP Sales",
                    source_url="https://acme.com/team/jane",
                    supporting_excerpt="Jane Doe is VP Sales.",
                )
            ],
            company=company,
        )[0]
        session.expunge(record)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="The team page changed."))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await refresh_evidence_record(client, record)
    assert result.status == "changed"

