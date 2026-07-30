"""Paths and constants. No environment variables required for normal use."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
INBOX_DIR = PROJECT_ROOT / "inbox"
PROCESSED_DIR = INBOX_DIR / "processed"
BACKUP_DIR = PROJECT_ROOT / "backups"
REJECTS_PATH = DATA_DIR / "rejects.jsonl"

DEFAULT_PORT = 8765
HOST = "127.0.0.1"  # localhost only, never exposed to the network

# Tests point CRM_DB_PATH at a throwaway file; normal use never sets it.
DB_PATH = Path(os.environ.get("CRM_DB_PATH", DATA_DIR / "crm.db"))

# How many days out the "No answer" quick action schedules the retry.
NO_ANSWER_RETRY_DAYS = 2
# Keep this many database backups before pruning the oldest.
BACKUP_KEEP = 14


def ensure_dirs() -> None:
    for path in (DATA_DIR, INBOX_DIR, PROCESSED_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)
