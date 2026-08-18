"""Copy repo data into a CRM_HOME folder (never overwrites an existing DB)."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config

HOME_PREFIX = "ProspectingCRM"
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,60}$")


def init_home(
    home: Path,
    *,
    source_data: Path | None = None,
    source_inbox: Path | None = None,
    source_backups: Path | None = None,
    copy_data: bool = True,
) -> dict[str, str]:
    """Create home/data, home/inbox, home/backups and optionally seed from the repo.

    Returns a dict of human-readable status lines keyed by area.
    """
    home = home.expanduser().resolve()
    data = home / "data"
    inbox = home / "inbox"
    backups = home / "backups"
    photos = data / "photos"
    for path in (data, inbox, inbox / "processed", backups, photos):
        path.mkdir(parents=True, exist_ok=True)

    src_data = (source_data or (config.PROJECT_ROOT / "data")).resolve()
    src_inbox = (source_inbox or (config.PROJECT_ROOT / "inbox")).resolve()
    src_backups = (source_backups or (config.PROJECT_ROOT / "backups")).resolve()

    report: dict[str, str] = {
        "home": str(home),
        "data": str(data),
        "inbox": str(inbox),
        "backups": str(backups),
    }

    dest_db = data / "crm.db"
    src_db = src_data / "crm.db"
    if not copy_data:
        report["database"] = "skipped (folders only)"
    elif dest_db.exists():
        report["database"] = f"kept existing {dest_db}"
    elif src_db.exists():
        shutil.copy2(src_db, dest_db)
        # SQLite sidecars if the app was mid-write
        for suffix in ("-wal", "-shm"):
            side = Path(str(src_db) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(dest_db) + suffix))
        report["database"] = f"copied from {src_db}"
    else:
        report["database"] = "no source crm.db yet (empty home is fine)"

    src_photos = src_data / "photos"
    if copy_data and src_photos.is_dir():
        copied = 0
        for item in src_photos.iterdir():
            if not item.is_file():
                continue
            target = photos / item.name
            if target.exists():
                continue
            shutil.copy2(item, target)
            copied += 1
        report["photos"] = f"copied {copied} new file(s)" if copied else "photos already present or empty"
    else:
        report["photos"] = "skipped"

    src_rejects = src_data / "rejects.jsonl"
    dest_rejects = data / "rejects.jsonl"
    if copy_data and src_rejects.exists() and not dest_rejects.exists():
        shutil.copy2(src_rejects, dest_rejects)
        report["rejects"] = "copied"
    else:
        report["rejects"] = "kept or none"

    # Inbox: copy pending JSON only if home inbox has no files yet
    pending = [p for p in inbox.glob("*.json") if p.is_file()]
    if copy_data and not pending and src_inbox.is_dir():
        n = 0
        for item in src_inbox.glob("*.json"):
            if item.is_file():
                shutil.copy2(item, inbox / item.name)
                n += 1
        report["inbox_files"] = f"copied {n} pending deposit(s)" if n else "none pending"
    else:
        report["inbox_files"] = "kept existing or skipped"

    if copy_data and src_backups.is_dir() and not any(backups.glob("crm-*.db")):
        n = 0
        for item in sorted(src_backups.glob("crm-*.db"))[-5:]:
            shutil.copy2(item, backups / item.name)
            n += 1
        report["backup_files"] = f"copied {n} recent backup(s)" if n else "none"
    else:
        report["backup_files"] = "kept existing or skipped"

    return report


def display_name(path: Path) -> str:
    """Human label: ProspectingCRM stays as-is; ProspectingCRM-Dental → Dental."""
    name = path.name
    if name == HOME_PREFIX:
        return HOME_PREFIX
    prefix = HOME_PREFIX + "-"
    if name.startswith(prefix):
        return name[len(prefix):].replace("-", " ")
    return name


def folder_name_from_label(raw: str) -> str:
    """Turn a typed name into a Documents folder: 'Dental' → ProspectingCRM-Dental."""
    label = " ".join((raw or "").split())
    if not label:
        raise ValueError("Give this list a short name.")
    if not _SAFE_LABEL.match(label):
        raise ValueError("Use letters, numbers, spaces, or hyphens only.")
    if label.lower() == HOME_PREFIX.lower():
        return HOME_PREFIX
    if label.lower().startswith(HOME_PREFIX.lower() + "-"):
        rest = label.split("-", 1)[1]
        slug = re.sub(r"[\s_]+", "-", rest).strip("-")
        if not slug:
            raise ValueError("Give this list a short name.")
        return f"{HOME_PREFIX}-{slug}"
    slug = re.sub(r"[\s_]+", "-", label).strip("-")
    return f"{HOME_PREFIX}-{slug}"


def resolve_listed_home(name: str, parent: Path | None = None) -> Path:
    """Resolve a picker folder name under Documents. Rejects path tricks."""
    parent = (parent or config.HOMES_PARENT).expanduser().resolve()
    name = (name or "").strip()
    if name != HOME_PREFIX and not name.startswith(HOME_PREFIX + "-"):
        raise ValueError("That is not a calling-list folder.")
    if "/" in name or "\\" in name or name in {".", ".."} or ".." in name:
        raise ValueError("That is not a calling-list folder.")
    home = (parent / name).resolve()
    if os.path.normcase(str(home.parent)) != os.path.normcase(str(parent)):
        raise ValueError("That is not a calling-list folder.")
    return home


def list_homes(parent: Path | None = None) -> list[dict]:
    """ProspectingCRM and ProspectingCRM-* folders under Documents."""
    parent = (parent or config.HOMES_PARENT).expanduser().resolve()
    if not parent.is_dir():
        return []
    homes = []
    for item in sorted(parent.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir():
            continue
        if item.name != HOME_PREFIX and not item.name.startswith(HOME_PREFIX + "-"):
            continue
        homes.append(describe_home(item))
    return homes


def describe_home(path: Path) -> dict:
    db = path / "data" / "crm.db"
    has_database = db.is_file()
    updated = None
    updated_label = "No database yet"
    if has_database:
        updated = datetime.fromtimestamp(db.stat().st_mtime)
        updated_label = updated.strftime("%b %d, %Y")
    count = _count_prospects(db) if has_database else None
    people = None
    if count is None and has_database:
        people = "List on disk"
    elif count is None:
        people = "Empty — ready to fill"
    elif count == 1:
        people = "1 person"
    else:
        people = f"{count} people"
    return {
        "name": path.name,
        "path": str(path),
        "label": display_name(path),
        "has_database": has_database,
        "prospect_count": count,
        "people": people,
        "updated": updated,
        "updated_label": updated_label,
        "is_default": path.name == HOME_PREFIX,
    }


def attach_home(app, home: Path) -> None:
    """Create folders if needed, point config at this home, open its database."""
    from .backup import auto_backup_if_stale
    from .db import build_session_factory, create_db_engine, initialize

    init_home(home, copy_data=False)
    config.apply_home(home)
    old = getattr(app.state, "engine", None)
    if old is not None:
        old.dispose()
    engine = create_db_engine()
    initialize(engine)
    app.state.engine = engine
    app.state.factory = build_session_factory(engine)
    app.state.home_ready = True
    auto_backup_if_stale()


def _count_prospects(db_path: Path) -> int | None:
    try:
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='prospects'"
            ).fetchone()
            if not row:
                return 0
            count = connection.execute("SELECT COUNT(*) FROM prospects").fetchone()
            return int(count[0]) if count else 0
        finally:
            connection.close()
    except sqlite3.Error:
        return None
