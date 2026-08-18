"""List picker: choose or create a Documents calling-list home."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def picker_client(tmp_path, monkeypatch):
    from crm import config
    from crm.web.app import create_app

    monkeypatch.setattr(config, "HOMES_PARENT", tmp_path)
    monkeypatch.setattr(config, "CRM_HOME", config.CRM_HOME)
    monkeypatch.setattr(config, "DATA_DIR", config.DATA_DIR)
    monkeypatch.setattr(config, "INBOX_DIR", config.INBOX_DIR)
    monkeypatch.setattr(config, "PROCESSED_DIR", config.PROCESSED_DIR)
    monkeypatch.setattr(config, "BACKUP_DIR", config.BACKUP_DIR)
    monkeypatch.setattr(config, "REJECTS_PATH", config.REJECTS_PATH)
    monkeypatch.setattr(config, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(config, "PHOTOS_DIR", config.PHOTOS_DIR)

    return TestClient(create_app(pick_home=True), follow_redirects=False)


def test_picker_redirects_until_a_list_is_opened(picker_client):
    response = picker_client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/homes"
    page = picker_client.get("/homes")
    assert page.status_code == 200
    assert "Which list do you want to open?" in page.text
    assert "Start a new list" in page.text


def test_create_and_open_new_list(picker_client, tmp_path):
    response = picker_client.post("/homes/new", data={"name": "Dental"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings")
    home = tmp_path / "ProspectingCRM-Dental"
    assert (home / "data").is_dir()
    assert (home / "data" / "crm.db").is_file()
    today = picker_client.get("/")
    assert today.status_code == 200
    assert "Today" in today.text


def test_open_existing_list(picker_client, tmp_path):
    (tmp_path / "ProspectingCRM").mkdir()
    page = picker_client.get("/homes")
    assert "ProspectingCRM" in page.text
    assert "Usual list" in page.text

    opened = picker_client.post("/homes/open", data={"name": "ProspectingCRM"})
    assert opened.status_code == 303
    assert opened.headers["location"] == "/"
    today = picker_client.get("/")
    assert today.status_code == 200
    assert "Today" in today.text


def test_open_rejects_path_escape(picker_client):
    response = picker_client.post("/homes/open", data={"name": "../secret"})
    assert response.status_code == 200
    assert "not a calling-list folder" in response.text


def test_create_duplicate_existing_db_asks_to_open(picker_client, tmp_path):
    from crm.home import attach_home

    attach_home(picker_client.app, tmp_path / "ProspectingCRM-Dental")
    # Detach so the picker still requires a choice on a *new* client? Same
    # client is already home_ready; creating again should still error.
    response = picker_client.post("/homes/new", data={"name": "Dental"})
    assert response.status_code == 200
    assert "already exists" in response.text
