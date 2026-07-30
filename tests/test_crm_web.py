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
    assert "Today's calls" in response.text


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


def test_enrich_request_from_filtered_view_skips_closed(client):
    add_prospect(client)
    add_prospect(client, full_name="Nope Person", company_name="Nope Co",
                 company_domain="nope.example", status="not_fit",
                 phone="(305) 555-0113", email="nope@nope.example")
    created = client.post("/requests/enrich", data={"q": "", "status": "", "region": "",
                                                    "priority": "", "min_score": "", "due": ""})
    brief = client.get(created.headers["location"])
    assert "Jane Doe" in brief.text
    assert "Nope Person" not in brief.text  # closed statuses are skipped


def test_note_append_only(client):
    location = add_prospect(client).split("?")[0]
    prospect_id = location.rsplit("/", 1)[1]
    client.post(f"/prospects/{prospect_id}/notes", data={"body": "Great chat about entitlement delays"})
    detail = client.get(location)
    assert "Great chat about entitlement delays" in detail.text
