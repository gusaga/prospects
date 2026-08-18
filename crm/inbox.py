"""The watched inbox/ folder: any .json dropped there is a deposit."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from . import config
from .ingest import IngestSummary, ingest_deposit_json


def pending_files() -> list:
    config.ensure_dirs()
    return sorted(p for p in config.INBOX_DIR.glob("*.json") if p.is_file())


def sweep_inbox(session: Session) -> list[IngestSummary]:
    """Ingest every waiting deposit, then move each file to inbox/processed/."""
    summaries: list[IngestSummary] = []
    for path in pending_files():
        summary = ingest_deposit_json(session, path.read_text(encoding="utf-8"), filename=path.name)
        summaries.append(summary)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path.rename(config.PROCESSED_DIR / f"{stamp}-{path.name}")
    return summaries
