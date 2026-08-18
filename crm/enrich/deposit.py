"""Write Stage-2 enricher deposits into inbox/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .engine import EnrichResult


def write_enrich_deposit(
    result: EnrichResult,
    *,
    inbox_dir: Path | None = None,
    batch_note: str | None = None,
) -> Path | None:
    """Write a schema_version 2 deposit file. Returns path, or None if nothing to deposit."""
    records = result.records
    if not records:
        return None
    config.ensure_dirs()
    inbox = inbox_dir or config.INBOX_DIR
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    req_part = f"-R{result.request_id}" if result.request_id else ""
    path = inbox / f"enrich-{stamp}{req_part}.json"
    payload = {
        "schema_version": 3,
        "source": "enricher",
        "request_id": result.request_id,
        "batch_note": batch_note
        or f"Local enricher run for {len(records)} prospect(s)",
        "shortfall_reasons": result.shortfall_reasons,
        "prospects": records,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
