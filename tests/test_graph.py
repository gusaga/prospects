"""Tests for BDR network spider / atlas builders and API."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from crm.db import initialize
from crm.graph import build_atlas_graph, build_company_graph, infer_buying_role
from crm.ingest import ingest_records
from crm.models import Prospect


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("crm.config.INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr("crm.config.PROCESSED_DIR", tmp_path / "inbox" / "processed")
    monkeypatch.setattr("crm.config.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("crm.config.REJECTS_PATH", tmp_path / "data" / "rejects.jsonl")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "data").mkdir()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    initialize(engine)
    with Session(engine) as sess:
        yield sess


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from crm import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "REJECTS_PATH", tmp_path / "rejects.jsonl")
    monkeypatch.setattr(config, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(config, "PROCESSED_DIR", tmp_path / "inbox" / "processed")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")

    from fastapi.testclient import TestClient

    from crm.web.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def add_prospect(client, **overrides) -> str:
    form = {
        "full_name": "Jane Doe",
        "title": "VP of Land Development",
        "company_name": "Acme Communities",
        "company_domain": "acme.example",
        "phone": "(512) 555-0100",
        "email": "jane@acme.example",
        "linkedin_url": "",
        "region": "Texas",
        "icp_score": "85",
        "icp_rationale": "Great fit",
        "notes": "",
        "status": "queued",
        "priority": "3",
    }
    form.update(overrides)
    response = client.post("/prospects/new", data=form)
    assert response.status_code == 303
    return response.headers["location"]


def _seed_company_with_two(session: Session):
    ingest_records(
        session,
        [
            {
                "company": {
                    "name": "Maronda Homes",
                    "domain": "marondahomes.example",
                    "region": "Florida",
                },
                "full_name": "Wayne Broedel",
                "title": "Division President",
                "city": "Winter Garden",
                "region": "Florida",
                "icp_score": 90,
                "icp_rationale": "fit",
                "linkedin_url": "https://www.linkedin.com/in/wayne-broedel-b3196229",
                "phone": "(407) 555-0100",
                "evidence": [{"url": "https://orangecsf.org/about/", "note": "bio"}],
                "status": "queued",
            },
            {
                "company": {
                    "name": "Maronda Homes",
                    "domain": "marondahomes.example",
                    "region": "Florida",
                },
                "full_name": "Alex Neighbor",
                "title": "VP Land",
                "city": "Orlando",
                "region": "Florida",
                "icp_score": 80,
                "icp_rationale": "fit",
                "evidence": [{"url": "https://www.marondahomes.example/team", "note": "team"}],
                "status": "queued",
            },
            {
                "company": {"name": "Other Co", "domain": "other.example", "region": "Texas"},
                "full_name": "Lone Star",
                "title": "VP",
                "city": "Austin",
                "region": "Texas",
                "icp_score": 70,
                "icp_rationale": "fit",
                "evidence": [{"url": "https://other.example/t", "note": "t"}],
                "status": "queued",
            },
        ],
        filename="graph-seed.json",
        source="codex",
    )
    return session.scalars(select(Prospect).where(Prospect.full_name == "Wayne Broedel")).one()


def test_infer_buying_role_vp():
    assert infer_buying_role("VP of Land Development")["slug"] == "economic_buyer"
    assert infer_buying_role("Senior Land Manager")["slug"] in {"influencer", "coach"}


def test_build_company_spider_has_roles_and_gaps(session):
    wayne = _seed_company_with_two(session)
    graph = build_company_graph(session, wayne.company_id, focus_prospect_id=wayne.id)
    assert graph["layout"] == "spider"
    assert graph["company"]["name"] == "Maronda Homes"
    assert len(graph["people"]) == 2
    assert any(p.get("focus") for p in graph["people"])
    assert any(p.get("role") == "economic_buyer" for p in graph["people"])
    # Two contacts with phones/linkedin — may still flag land title depending on titles
    assert isinstance(graph["gaps"], list)
    assert graph["focus_id"] == f"person:{wayne.id}"
    assert any(e["type"] == "works_at" for e in graph["edges"])


def test_build_atlas_lists_accounts_by_region(session):
    _seed_company_with_two(session)
    atlas = build_atlas_graph(session, region="Florida")
    assert atlas["layout"] == "atlas"
    assert atlas["meta"]["account_count"] == 1
    assert atlas["accounts"][0]["name"] == "Maronda Homes"
    assert atlas["accounts"][0]["person_count"] == 2
    labels = {n["label"] for n in atlas["nodes"] if n["type"] == "person"}
    assert "Lone Star" not in labels
    assert "Wayne Broedel" in labels


def test_api_spider_and_atlas_pages(client):
    loc = add_prospect(
        client,
        full_name="Graph One",
        company_name="Graph Co",
        company_domain="graphco.example",
        region="Texas",
    )
    detail = client.get(loc.split("?")[0])
    assert detail.status_code == 200
    assert "Coverage gaps" in detail.text
    assert "data-network-map" in detail.text
    assert "/static/network-map.js" in detail.text
    assert "three.min.js" not in detail.text

    pid = loc.split("?")[0].rsplit("/", 1)[1]
    atlas = client.get("/api/graph?scope=atlas")
    assert atlas.status_code == 200
    body = atlas.json()
    assert body["layout"] == "atlas"
    assert body["accounts"]
    cid = body["accounts"][0]["company_id"]
    company_graph = client.get(f"/api/graph?company_id={cid}&focus_prospect_id={pid}")
    assert company_graph.status_code == 200
    cg = company_graph.json()
    assert cg["layout"] == "spider"
    assert cg["people"]

    page = client.get("/network")
    assert page.status_code == 200
    assert "Network atlas" in page.text
    assert 'data-scope="atlas"' in page.text


def test_api_graph_requires_company_id_for_company_scope(client):
    response = client.get("/api/graph")
    assert response.status_code == 400


def test_single_contact_flags_single_threaded_gap(session):
    ingest_records(
        session,
        [{
            "company": {"name": "Solo Co", "domain": "solo.example", "region": "Texas"},
            "full_name": "Only Person",
            "title": "Analyst",
            "icp_score": 50,
            "icp_rationale": "x",
            "evidence": [{"url": "https://solo.example/t", "note": "t"}],
            "status": "queued",
        }],
        filename="solo.json",
        source="codex",
    )
    prospect = session.scalar(select(Prospect))
    graph = build_company_graph(
        session,
        prospect.company_id,
        focus_prospect_id=prospect.id,
        target_titles=["VP of Land Development"],
    )
    codes = {g["code"] for g in graph["gaps"]}
    assert "single_threaded" in codes
    assert "no_economic_buyer" in codes
    assert "no_target_title" in codes
