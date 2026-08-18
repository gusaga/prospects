"""Timestamped copies of the database file, pruned to the newest N."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import config


def backup_now() -> Path:
    config.ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = config.BACKUP_DIR / f"crm-{stamp}.db"
    # sqlite backup API copies safely even while the app holds the file open.
    source = sqlite3.connect(config.DB_PATH)
    dest = sqlite3.connect(target)
    with dest:
        source.backup(dest)
    dest.close()
    source.close()
    _prune()
    return target


def latest_backup() -> Path | None:
    backups = sorted(config.BACKUP_DIR.glob("crm-*.db"))
    return backups[-1] if backups else None


def auto_backup_if_stale(max_age_hours: int = 24) -> Path | None:
    """Called on app startup: keep a daily backup without any scheduler."""
    if not config.DB_PATH.exists():
        return None
    newest = latest_backup()
    if newest:
        age = datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)
        if age < timedelta(hours=max_age_hours):
            return None
    return backup_now()


def restore(backup_path: Path) -> None:
    """Replace the live database with a backup (app must be stopped)."""
    if not backup_path.exists():
        raise FileNotFoundError(backup_path)
    shutil.copy2(backup_path, config.DB_PATH)


def _prune() -> None:
    backups = sorted(config.BACKUP_DIR.glob("crm-*.db"))
    for old in backups[: -config.BACKUP_KEEP]:
        old.unlink()
