"""Route smoke tests through FastAPI's TestClient (no server needed)."""

from __future__ import annotations

from datetime import date

import pytest


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
        "full_name": "Jane Doe", "title": "VP of Land Development",
        "company_name": "Acme Communities", "company_domain": "acme.example",
        "phone": "(512) 555-0100", "email": "jane@acme.example",
        "linkedin_url": "", "region": "Texas", "icp_score": "85",
        "icp_rationale": "Great fit", "notes": "", "status": "queued", "priority": "3",
    }
    form.update(overrides)
    response = client.post("/prospects/new", data=form)
    assert response.status_code == 303
    return response.headers["location"]


def test_today_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Today" in response.text
    assert "Research desk" in response.text


def test_add_and_view_prospect(client):
    location = add_prospect(client)
    assert location.startswith("/prospects/")
    detail = client.get(location.split("?")[0])
    assert detail.status_code == 200
    assert "Jane Doe" in detail.text
    assert "(512) 555-0100" in detail.text


def test_queued_prospect_appears_on_today(client):
    add_prospect(client)
    response = client.get("/")
    assert "Jane Doe" in response.text


def test_call_outcome_one_click(client):
    location = add_prospect(client).split("?")[0]
    prospect_id = location.rsplit("/", 1)[1]
    response = client.post(f"/prospects/{prospect_id}/action",
                           data={"action": "no_answer", "note": "", "when": ""},
                           headers={"referer": location})
    assert response.status_code == 303
    detail = client.get(location)
    assert "Called — no answer" in detail.text
    assert "Retry" in detail.text  # activity logged


def test_inline_edit_patch(client):
    location = add_prospect(client).split("?")[0]
    prospect_id = location.rsplit("/", 1)[1]
    response = client.patch(f"/api/prospects/{prospect_id}",
                            json={"field": "phone", "value": "(737) 555-0199"})
    assert response.status_code == 200 and response.json()["ok"]
    assert "(737) 555-0199" in client.get(location).text

    bad = client.patch(f"/api/prospects/{prospect_id}", json={"field": "icp_score", "value": "999"})
    assert bad.status_code == 400


def test_followup_action_sets_date_and_today_shows_due(client):
    location = add_prospect(client).split("?")[0]
    prospect_id = location.rsplit("/", 1)[1]
    client.post(f"/prospects/{prospect_id}/action",
                data={"action": "follow_up", "note": "call back", "when": date.today().isoformat()})
    today = client.get("/")
    assert "Due follow-ups" in today.text


def test_search_and_filters(client):
    add_prospect(client)
    add_prospect(client, full_name="Bob Builder", company_name="Other Homes",
                 company_domain="other.example", status="new", region="Florida",
                 phone="(305) 555-0142", email="bob@other.example")
    hits = client.get("/prospects", params={"q": "Builder"})
    assert "Bob Builder" in hits.text and "Jane Doe" not in hits.text
    by_status = client.get("/prospects", params={"status": "queued"})
    assert "Jane Doe" in by_status.text and "Bob Builder" not in by_status.text
    partial = client.get("/prospects", params={"q": "Jane", "partial": "1"})
    assert "<table" in partial.text and "<html" not in partial.text.lower()


def test_prospects_table_shows_enrich_mark(client):
    from crm.db import build_session_factory, create_db_engine, initialize, session_scope
    from crm.ingest import ingest_records
    from crm.models import Prospect
    from sqlalchemy import select

    loc = add_prospect(client, full_name="Needs Depth", phone="")
    pid = int(loc.split("?")[0].rsplit("/", 1)[1])
    before = client.get("/prospects", params={"q": "Needs Depth"})
    assert "is-pending" in before.text
    assert "Not enriched yet" in before.text

    engine = create_db_engine()
    initialize(engine)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        prospect = session.get(Prospect, pid)
        ingest_records(
            session,
            [{
                "prospect_id": pid,
                "full_name": prospect.full_name,
                "company": {"name": prospect.company.name, "domain": prospect.company.domain},
                "phone": "(512) 555-9999",
                "city": "Austin",
                "evidence": [{"url": "https://example.com/team", "note": "team"}],
            }],
            filename="enrich-mark.json",
            source="enricher",
        )

    after = client.get("/prospects", params={"q": "Needs Depth"})
    assert "is-enriched" in after.text
    assert "Stage-2 research applied" in after.text


def test_csv_export(client):
    add_prospect(client)
    response = client.get("/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Jane Doe" in response.text
    assert "acme.example" in response.text


def test_duplicate_manual_add_redirects_to_existing(client):
    first = add_prospect(client).split("?")[0]
    second = client.post("/prospects/new", data={
        "full_name": "Jane Doe", "company_name": "Acme Communities",
        "company_domain": "acme.example", "title": "", "phone": "", "email": "",
        "linkedin_url": "", "region": "", "icp_score": "", "icp_rationale": "",
        "notes": "", "status": "new", "priority": "2",
    })
    assert second.status_code == 303
    assert second.headers["location"].split("?")[0] == first


def test_research_request_flow(client):
    created = client.post("/requests", data={
        "count": "7", "region_focus": "Houston metro", "title_focus": "", "notes": "",
    })
    assert created.status_code == 303
    brief_url = created.headers["location"]
    brief = client.get(brief_url)
    assert brief.status_code == 200
    assert "Research 7 new cold-call prospects" in brief.text
    assert "Houston metro" in brief.text
    assert '"request_id": 1' in brief.text

    page = client.get("/import")
    assert "R-1" in page.text and "0 / 7 delivered" in page.text

    closed = client.post("/requests/1/close")
    assert closed.status_code == 303
    assert "R-1" not in client.get("/import").text


def test_enrich_request_from_detail_page(client):
    location = add_prospect(client).split("?")[0]
    prospect_id = location.rsplit("/", 1)[1]
    created = client.post("/requests/enrich", data={"prospect_id": prospect_id})
    assert created.status_code == 303
    brief = client.get(created.headers["location"])
    assert f"prospect_id {prospect_id}: Jane Doe" in brief.text
    page = client.get("/import")
    assert "Enrichment" in page.text and "0 / 1 enriched" in page.text


def test_enrich_request_from_selected_ids_skips_closed(client):
    open_loc = add_prospect(client).split("?")[0]
    open_id = open_loc.rsplit("/", 1)[1]
    closed_loc = add_prospect(
        client, full_name="Nope Person", company_name="Nope Co",
        company_domain="nope.example", status="not_fit",
        phone="(305) 555-0113", email="nope@nope.example",
    ).split("?")[0]
    closed_id = closed_loc.rsplit("/", 1)[1]
    created = client.post(
        "/requests/enrich",
        data={"prospect_ids": f"{open_id},{closed_id}"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]
    assert location.startswith("/requests/") and location.endswith("/brief")
    brief = client.get(location)
    assert brief.headers["content-type"].startswith("text/plain")
    assert "Jane Doe" in brief.text
    assert "Nope Person" not in brief.text
    assert "SEARCH ANCHORS" in brief.text
    assert "NARROW THE SEARCH" in brief.text


def test_enrich_request_requires_explicit_selection(client):
    add_prospect(client)
    created = client.post("/requests/enrich", data={})
    assert created.status_code == 303
    assert "Select+one+or+more" in created.headers["location"]


def test_run_enricher_endpoint_returns_results_page_not_brief(client, monkeypatch):
    from crm.enrich.engine import EnrichResult, ProspectEnrichment

    location = add_prospect(client).split("?")[0]
    prospect_id = int(location.rsplit("/", 1)[1])

    def fake_enrich(session, ids, request_id=None, client=None):
        return EnrichResult(
            request_id=request_id,
            items=[
                ProspectEnrichment(
                    prospect_id=prospect_id,
                    full_name="Jane Doe",
                    company_name="Acme Homes",
                    pages_fetched=2,
                    shortfalls=["no new public facts found"],
                )
            ],
        )

    monkeypatch.setattr("crm.enrich.enrich_prospects", fake_enrich)
    monkeypatch.setattr("crm.enrich.write_enrich_deposit", lambda *a, **k: None)

    response = client.post(
        "/requests/enrich/run",
        data={"prospect_id": str(prospect_id)},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Enricher results" in response.text
    assert "Jane Doe" in response.text
    assert "No deposit written" in response.text


def test_note_append_only(client):
    location = add_prospect(client).split("?")[0]
    prospect_id = location.rsplit("/", 1)[1]
    client.post(f"/prospects/{prospect_id}/notes", data={"body": "Great chat about entitlement delays"})
    detail = client.get(location)
    assert "Great chat about entitlement delays" in detail.text
