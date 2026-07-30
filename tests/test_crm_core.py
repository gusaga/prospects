"""Tests for the new CRM: dedupe rules, the ingest pipeline, seed/wipe."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from crm import config
from crm.dedupe import find_near_duplicate, name_key, normalize_domain
from crm.ingest import ingest_csv, ingest_deposit_json, ingest_records
from crm.models import Base, Company, DupeReview, Prospect
from crm.seed import seed, wipe


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REJECTS_PATH", tmp_path / "rejects.jsonl")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(config, "PROCESSED_DIR", tmp_path / "inbox" / "processed")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
        s.rollback()


def record(**overrides):
    base = {
        "company": {"name": "Acme Communities", "domain": "acme.example"},
        "full_name": "Jane Doe",
        "title": "VP of Land Development",
        "icp_score": 85,
        "icp_rationale": "Perfect title at an owner/developer",
        "evidence": [{"url": "https://acme.example/team", "note": "team page"}],
    }
    base.update(overrides)
    return base


# ---- normalization -----------------------------------------------------

def test_name_key_strips_initials_and_suffixes():
    assert name_key("D. Dean Dumke, Jr.") == name_key("D Dean Dumke")
    assert name_key("Jane  M. Doe") == name_key("Jane Doe")
    assert name_key("Mike  O'Brien") == name_key("Mike OBrien")
    assert name_key("José Núñez") == name_key("Jose Nunez")


def test_normalize_domain():
    assert normalize_domain("https://www.Acme.com/team?x=1") == "acme.com"
    assert normalize_domain("acme.com") == "acme.com"
    assert normalize_domain("") is None
    assert normalize_domain(None) is None


# ---- ingest pipeline ----------------------------------------------------

def test_ingest_creates_prospect_and_company(session):
    summary = ingest_records(session, [record()], filename="t.json", source="codex")
    assert summary.created == 1 and summary.rejected == 0
    prospect = session.scalar(select(Prospect))
    assert prospect.full_name == "Jane Doe"
    assert prospect.company.domain == "acme.example"
    assert prospect.status == "new"
    assert prospect.evidence[0]["url"] == "https://acme.example/team"


def test_exact_duplicate_skipped_and_enriched(session):
    ingest_records(session, [record()], filename="a.json", source="codex")
    # Same person again, now with a phone -> enriched, not duplicated.
    summary = ingest_records(session, [record(phone="(512) 555-0101")], filename="b.json", source="codex")
    assert summary.enriched == 1 and summary.created == 0
    prospect = session.scalar(select(Prospect))
    assert prospect.phone == "(512) 555-0101"
    # Third time with nothing new -> plain duplicate.
    summary = ingest_records(session, [record(phone="(512) 555-0101")], filename="c.json", source="codex")
    assert summary.duplicates == 1 and summary.enriched == 0
    assert session.scalar(select(Prospect.id).order_by(Prospect.id.desc())) == prospect.id


def test_nickname_near_duplicate_goes_to_review(session):
    ingest_records(session, [record(full_name="Michael Cronin")], filename="a.json", source="codex")
    summary = ingest_records(session, [record(full_name="Mike Cronin")], filename="b.json", source="codex")
    assert summary.review == 1 and summary.created == 0
    review = session.scalar(select(DupeReview))
    assert review.status == "pending"
    assert "similar name" in review.reason


def test_same_email_elsewhere_is_near_duplicate(session):
    ingest_records(session, [record(email="jane@acme.example")], filename="a.json", source="codex")
    other = record(
        company={"name": "Different Corp", "domain": "different.example"},
        full_name="J. Doe", email="jane@acme.example",
    )
    summary = ingest_records(session, [other], filename="b.json", source="codex")
    assert summary.review == 1


def test_invalid_record_rejected_with_reason(session):
    bad = record(email="not-an-email", icp_score=250)
    summary = ingest_records(session, [bad], filename="bad.json", source="codex")
    assert summary.rejected == 1 and summary.created == 0
    lines = config.REJECTS_PATH.read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert "email" in entry["reason"] and "icp_score" in entry["reason"]


def test_deposit_envelope_validation(session):
    good = json.dumps({"schema_version": 1, "prospects": [record()]})
    summary = ingest_deposit_json(session, good, filename="deposit.json")
    assert summary.created == 1

    wrong_version = json.dumps({"schema_version": 99, "prospects": [record()]})
    summary = ingest_deposit_json(session, wrong_version, filename="v99.json")
    assert summary.rejected == 1 and summary.created == 0

    summary = ingest_deposit_json(session, "{not json", filename="broken.json")
    assert summary.rejected == 1


def test_csv_round_trip(session):
    csv_text = (
        "company,company_domain,full_name,title,phone,email,linkedin_url,region,industry,"
        "size_band,icp_score,icp_rationale,status,priority,notes,next_followup_on,evidence_urls\n"
        'Acme Communities,acme.example,Jane Doe,VP of Land Development,(512) 555-0100,jane@acme.example,'
        ',Texas,Land development,11-50,85,Great fit,queued,3,Warm intro,2026-08-05,'
        "https://acme.example/team|https://acme.example/news\n"
    )
    summary = ingest_csv(session, csv_text.encode(), filename="import.csv")
    assert summary.created == 1, summary.messages
    prospect = session.scalar(select(Prospect))
    assert prospect.status == "queued"
    assert prospect.priority == 3
    assert prospect.next_followup_on.isoformat() == "2026-08-05"
    assert len(prospect.evidence) == 2


# ---- seed ---------------------------------------------------------------

def test_seed_and_wipe(session):
    created = seed(session)
    assert created == 25
    assert seed(session) == 0  # idempotent
    assert session.scalar(select(Prospect.source)) == "seed"
    removed = wipe(session)
    assert removed == 25
    assert session.scalar(select(Prospect)) is None
    assert session.scalar(select(Company)) is None
