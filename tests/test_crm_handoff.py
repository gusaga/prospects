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


def test_deleting_prospect_removes_its_dupe_reviews(session):
    from crm.seed import seed, wipe

    seed(session)
    ingest_records(session, [record(
        company={"name": "Sunrise Example Communities", "domain": "sunrise-communities.example"},
        full_name="Peter Prototype",
    )], filename="x.json", source="codex")
    review = session.scalar(select(DupeReview))
    assert review is not None
    resolve_dupe(session, review, "discarded")

    wipe(session)  # must not trip the dupe_reviews foreign key
    session.flush()
    assert session.scalar(select(Prospect.source).where(Prospect.source == "seed")) is None


def test_deposit_tracks_request_and_shortfall(session):
    from crm.ingest import ingest_deposit_json
    from crm.models import ImportBatch, ResearchRequest

    req = ResearchRequest(requested_count=5)
    session.add(req)
    session.flush()

    deposit = json.dumps({
        "schema_version": 1,
        "request_id": req.id,
        "shortfall_reasons": ["only 1 verifiable candidate", "only 1 verifiable candidate"],
        "prospects": [record()],
    })
    summary = ingest_deposit_json(session, deposit, filename="r.json")

    assert summary.created == 1
    batch = session.scalar(select(ImportBatch).where(ImportBatch.request_id == req.id))
    assert batch is not None and batch.created_count == 1
    assert req.shortfall == ["only 1 verifiable candidate"]  # deduplicated


def test_deposit_with_unknown_request_id_still_imports(session):
    from crm.ingest import ingest_deposit_json

    deposit = json.dumps({"schema_version": 1, "request_id": 999, "prospects": [record()]})
    summary = ingest_deposit_json(session, deposit, filename="r.json")
    assert summary.created == 1
    assert any("does not exist" in m for m in summary.messages)


def test_dry_run_predicts_without_writing(session):
    from crm.ingest import dry_run_deposit
    from crm.models import ImportBatch

    ingest_records(session, [record()], filename="a.json", source="codex")
    mixed = json.dumps({"schema_version": 1, "prospects": [
        record(),                                        # exact duplicate
        record(full_name="Janie Doe"),                   # near-duplicate
        record(full_name="Sam New", company={"name": "Fresh Co", "domain": "fresh.example"}),
        record(full_name="X", icp_score=999),            # invalid
    ]})
    outcomes = [o for _, o, _ in dry_run_deposit(session, mixed)]
    assert outcomes == ["enrich-or-duplicate", "review", "create", "invalid"]
    # Nothing was written: still one prospect, one batch.
    assert session.scalar(select(Prospect.full_name)) == "Jane Doe"
    assert len(list(session.scalars(select(ImportBatch)))) == 1

    assert dry_run_deposit(session, "{broken")[0][1] == "envelope-invalid"


def test_example_deposit_file_is_valid():
    from crm.ingest import DepositFile

    example = (config.PROJECT_ROOT / "schemas" / "example-deposit.json").read_text(encoding="utf-8")
    deposit = DepositFile.model_validate_json(example)
    assert deposit.schema_version == 1
    assert len(deposit.prospects) == 2
