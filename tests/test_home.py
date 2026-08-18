"""CRM_HOME / init-home — keep business data outside the git checkout."""

from __future__ import annotations

from pathlib import Path

from crm.home import init_home


def test_init_home_copies_db_once(tmp_path: Path):
    src = tmp_path / "repo"
    (src / "data" / "photos").mkdir(parents=True)
    (src / "inbox").mkdir()
    (src / "backups").mkdir()
    db = src / "data" / "crm.db"
    db.write_bytes(b"sqlite-fake")
    (src / "data" / "photos" / "1.jpg").write_bytes(b"img")

    home = tmp_path / "ProspectingCRM"
    report = init_home(
        home,
        source_data=src / "data",
        source_inbox=src / "inbox",
        source_backups=src / "backups",
    )
    dest = home / "data" / "crm.db"
    assert dest.exists()
    assert dest.read_bytes() == b"sqlite-fake"
    assert (home / "data" / "photos" / "1.jpg").exists()
    assert "copied" in report["database"]

    # Second run must not overwrite
    db.write_bytes(b"changed-source")
    report2 = init_home(
        home,
        source_data=src / "data",
        source_inbox=src / "inbox",
        source_backups=src / "backups",
    )
    assert dest.read_bytes() == b"sqlite-fake"
    assert "kept existing" in report2["database"]


def test_init_home_dirs_only(tmp_path: Path):
    src = tmp_path / "repo" / "data"
    src.mkdir(parents=True)
    (src / "crm.db").write_bytes(b"x")
    home = tmp_path / "empty-home"
    report = init_home(home, source_data=src, copy_data=False)
    assert (home / "data").is_dir()
    assert (home / "inbox").is_dir()
    assert not (home / "data" / "crm.db").exists()
    assert report["database"] == "skipped (folders only)"


def test_folder_name_from_label():
    from crm.home import folder_name_from_label

    assert folder_name_from_label("Dental") == "ProspectingCRM-Dental"
    assert folder_name_from_label("  Dental Clinics ") == "ProspectingCRM-Dental-Clinics"
    assert folder_name_from_label("ProspectingCRM") == "ProspectingCRM"
    try:
        folder_name_from_label("")
        assert False, "expected error"
    except ValueError:
        pass
    try:
        folder_name_from_label("../secret")
        assert False, "expected error"
    except ValueError:
        pass


def test_resolve_listed_home_rejects_escape(tmp_path):
    from crm.home import resolve_listed_home

    try:
        resolve_listed_home("..", parent=tmp_path)
        assert False, "expected error"
    except ValueError:
        pass
    try:
        resolve_listed_home("ProspectingCRM/../other", parent=tmp_path)
        assert False, "expected error"
    except ValueError:
        pass
    home = resolve_listed_home("ProspectingCRM-Dental", parent=tmp_path)
    assert home == (tmp_path / "ProspectingCRM-Dental").resolve()


def test_list_homes_finds_prefix_folders(tmp_path):
    from crm.home import list_homes

    (tmp_path / "ProspectingCRM").mkdir()
    (tmp_path / "ProspectingCRM-Dental").mkdir()
    (tmp_path / "Unrelated").mkdir()
    names = {item["name"] for item in list_homes(tmp_path)}
    assert names == {"ProspectingCRM", "ProspectingCRM-Dental"}
