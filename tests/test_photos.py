"""Tests for local prospect photo storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from crm.db import initialize
from crm.ingest import ingest_records
from crm.models import Prospect
from crm.photos import guess_image_urls_from_html, save_photo_bytes


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("crm.config.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("crm.config.PHOTOS_DIR", tmp_path / "data" / "photos")
    monkeypatch.setattr("crm.config.INBOX_DIR", tmp_path / "inbox")
    (tmp_path / "data").mkdir()
    (tmp_path / "inbox").mkdir()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    initialize(engine)
    with Session(engine) as sess:
        yield sess


def test_save_photo_bytes_and_ingest_photo_url(session, tmp_path, monkeypatch):
    # Minimal valid JPEG (1x1)
    jpeg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e"
        b"\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
        b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04"
        b"\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q"
        b"\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\x1f\xff\xd9"
    )
    # Simpler: use PNG magic via save_photo_bytes sniffing
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    ingest_records(
        session,
        [{
            "company": {"name": "Photo Co", "domain": "photo.example"},
            "full_name": "Pat Photo",
            "title": "VP",
            "icp_score": 70,
            "icp_rationale": "test",
            "evidence": [{"url": "https://photo.example/team", "note": "team"}],
            "status": "new",
        }],
        filename="p.json",
        source="manual",
    )
    prospect = session.scalar(select(Prospect))
    rel = save_photo_bytes(prospect.id, png, "image/png")
    assert rel == f"photos/{prospect.id}.png"
    assert (tmp_path / "data" / rel).is_file()
    prospect.photo_path = rel
    session.flush()

    # Enrich path via photo_url download mock
    from crm import photos as photos_mod

    def fake_download(url, prospect_id, client=None):
        return save_photo_bytes(prospect_id, png, "image/png")

    monkeypatch.setattr(photos_mod, "download_photo", fake_download)
    monkeypatch.setattr("crm.ingest.download_photo", fake_download, raising=False)
    prospect.photo_path = None
    session.flush()
    summary = ingest_records(
        session,
        [{
            "prospect_id": prospect.id,
            "company": {"name": "Photo Co", "domain": "photo.example"},
            "full_name": "Pat Photo",
            "photo_url": "https://photo.example/pat.png",
            "evidence": [{"url": "https://photo.example/team2", "note": "headshot page"}],
        }],
        filename="e.json",
        source="enricher",
    )
    assert summary.enriched == 1
    session.flush()
    session.refresh(prospect)
    assert prospect.photo_path


def test_guess_image_urls_prefers_named_headshot():
    html = """
    <html><body>
      <h2>Andon Calhoun</h2>
      <img src="/uploads/andon-calhoun-headshot.jpg" alt="Andon Calhoun" class="team-photo" width="240">
      <img src="/logo.png" alt="logo" class="site-logo" width="32">
    </body></html>
    """
    urls = guess_image_urls_from_html(
        html,
        "https://orangecsf.org/about/",
        "Andon Calhoun",
    )
    assert urls
    assert "andon-calhoun-headshot.jpg" in urls[0]
