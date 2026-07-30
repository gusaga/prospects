"""Tests for the Codex handoff kit: inbox sweep, dupe resolution, brief."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from crm import config
from crm.inbox import sweep_inbox
from crm.ingest import ingest_records, resolve_dupe
from crm.models import Base, DupeReview, Prospect


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REJECTS_PATH", tmp_path / "rejects.jsonl")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(config, "PROCESSED_DIR", tmp_path / "inbox" / "processed")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    config.ensure_dirs()
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
        "icp_rationale": "fits",
        "evidence": [{"url": "https://acme.example/team"}],
    }
    base.update(overrides)
    return base


def test_inbox_sweep_ingests_and_archives(session):
    deposit = {"schema_version": 1, "source": "codex", "prospects": [record()]}
    (config.INBOX_DIR / "batch.json").write_text(json.dumps(deposit), encoding="utf-8")

    summaries = sweep_inbox(session)

    assert len(summaries) == 1 and summaries[0].created == 1
    assert not list(config.INBOX_DIR.glob("*.json"))  # moved out of the inbox
    archived = list(config.PROCESSED_DIR.glob("*batch.json"))
    assert len(archived) == 1


def test_resolve_merge_fills_existing(session):
    ingest_records(session, [record()], filename="a.json", source="codex")
    ingest_records(session, [record(full_name="Janie Doe", phone="(512) 555-0001")],
                   filename="b.json", source="codex")
    review = session.scalar(select(DupeReview))
    assert review is not None

    message = resolve_dupe(session, review, "merged")

    assert "Merged" in message
    existing = session.scalar(select(Prospect))
    assert existing.phone == "(512) 555-0001"
    assert review.status == "resolved" and review.resolution == "merged"


def test_resolve_keep_both_creates_second_prospect(session):
    ingest_records(session, [record()], filename="a.json", source="codex")
    ingest_records(session, [record(full_name="Janie Doe")], filename="b.json", source="codex")
    review = session.scalar(select(DupeReview))

    resolve_dupe(session, review, "kept_both")

    names = sorted(session.scalars(select(Prospect.full_name)))
    assert names == ["Jane Doe", "Janie Doe"]


def test_resolve_discard_leaves_things_alone(session):
    ingest_records(session, [record()], filename="a.json", source="codex")
    ingest_records(session, [record(full_name="Janie Doe")], filename="b.json", source="codex")
    review = session.scalar(select(DupeReview))

    resolve_dupe(session, review, "discarded")

    assert session.scalar(select(Prospect.full_name)) == "Jane Doe"
    assert review.resolution == "discarded"


def test_brief_contains_icp_and_exclusions():
    from crm.web.routes_data import DEFAULT_ICP, build_brief

    brief = build_brief(DEFAULT_ICP, ["known.example", "other.example"])
    assert "VP of Land Development" in brief
    assert "Texas" in brief
    assert "known.example" in brief
    assert "schemas/prospect-deposit.schema.json" in brief
    assert "python -m crm import --inbox" in brief


def test_example_deposit_file_is_valid():
    from crm.ingest import DepositFile

    example = (config.PROJECT_ROOT / "schemas" / "example-deposit.json").read_text(encoding="utf-8")
    deposit = DepositFile.model_validate_json(example)
    assert deposit.schema_version == 1
    assert len(deposit.prospects) == 2
