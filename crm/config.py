"""Paths and constants. Environment overrides keep code and data separate."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


# CRM_HOME = one folder that holds data/, inbox/, and backups/ together.
# Individual CRM_*_DIR vars still win when set explicitly.
_HOME_RAW = os.environ.get("CRM_HOME", "").strip()
CRM_HOME: Path | None = Path(_HOME_RAW).expanduser().resolve() if _HOME_RAW else None

if CRM_HOME is not None:
    _default_data = CRM_HOME / "data"
    _default_inbox = CRM_HOME / "inbox"
    _default_backup = CRM_HOME / "backups"
else:
    _default_data = PROJECT_ROOT / "data"
    _default_inbox = PROJECT_ROOT / "inbox"
    _default_backup = PROJECT_ROOT / "backups"

DATA_DIR = _env_path("CRM_DATA_DIR", _default_data)
INBOX_DIR = _env_path("CRM_INBOX_DIR", _default_inbox)
PROCESSED_DIR = INBOX_DIR / "processed"
BACKUP_DIR = _env_path("CRM_BACKUP_DIR", _default_backup)
REJECTS_PATH = DATA_DIR / "rejects.jsonl"

DEFAULT_PORT = int(os.environ.get("CRM_PORT", "8765"))
# Local use defaults to loopback. Docker sets CRM_HOST=0.0.0.0 so the
# published port works — still runs on the friend's machine, not in the cloud.
HOST = os.environ.get("CRM_HOST", "127.0.0.1")

# Tests point CRM_DB_PATH at a throwaway file; normal use never sets it.
DB_PATH = _env_path("CRM_DB_PATH", DATA_DIR / "crm.db")

# How many days out the "No answer" quick action schedules the retry.
NO_ANSWER_RETRY_DAYS = 2
# Keep this many database backups before pruning the oldest.
BACKUP_KEEP = 14

PHOTOS_DIR = DATA_DIR / "photos"

APP_VERSION = os.environ.get("CRM_VERSION", "1.0.0")

# Suggested live home on Windows (used by run-live.bat / init-home).
DEFAULT_LIVE_HOME = Path.home() / "Documents" / "ProspectingCRM"
# Picker only lists ProspectingCRM* folders under this parent.
HOMES_PARENT = DEFAULT_LIVE_HOME.parent


def apply_home(home: Path) -> None:
    """Point this process at a Documents home (data/inbox/backups together)."""
    global CRM_HOME, DATA_DIR, INBOX_DIR, PROCESSED_DIR, BACKUP_DIR
    global REJECTS_PATH, DB_PATH, PHOTOS_DIR

    resolved = home.expanduser().resolve()
    CRM_HOME = resolved
    DATA_DIR = resolved / "data"
    INBOX_DIR = resolved / "inbox"
    BACKUP_DIR = resolved / "backups"
    PROCESSED_DIR = INBOX_DIR / "processed"
    REJECTS_PATH = DATA_DIR / "rejects.jsonl"
    DB_PATH = DATA_DIR / "crm.db"
    PHOTOS_DIR = DATA_DIR / "photos"


def ensure_dirs() -> None:
    for path in (DATA_DIR, INBOX_DIR, PROCESSED_DIR, BACKUP_DIR, PHOTOS_DIR):
        path.mkdir(parents=True, exist_ok=True)
