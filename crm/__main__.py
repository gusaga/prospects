"""Command line entry point: python -m crm <command>.

  serve      start the CRM at http://127.0.0.1:8765 (default command)
  import     deposit a prospects JSON file, or --inbox to sweep inbox/
  validate   dry-run a deposit file: report what import WOULD do, write nothing
  enrich     run the local web search/scrape enricher for selected prospects
  migrate    one-time copy of the legacy prospects.db into the new schema
  seed       add 25 clearly-fake sample prospects (--wipe removes them)
  status     row counts and pending-work summary
  backup     copy the database into backups/ now
  init-home  create CRM_HOME and copy data once (never overwrites)
  reset-new  mark every prospect as New / found today (keeps contact data)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import backup as backup_mod
from . import config
from .db import build_session_factory, create_db_engine, initialize, session_scope


def _factory():
    engine = create_db_engine()
    initialize(engine)
    return build_session_factory(engine)


def cmd_serve(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    import uvicorn

    from .web.app import create_app

    if not args.pick_home:
        backup_mod.auto_backup_if_stale()
    app = create_app(pick_home=args.pick_home)
    url = f"http://{config.HOST}:{args.port}"
    if args.pick_home:
        url = f"{url}/homes"
        print(f"Prospecting CRM — choose a list at {url}  (Ctrl+C to stop)")
    else:
        print(f"Prospecting CRM -> {url}  (Ctrl+C to stop)")
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=config.HOST, port=args.port, log_level="warning")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from .inbox import sweep_inbox
    from .ingest import ingest_deposit_json

    factory = _factory()
    if args.inbox:
        with session_scope(factory) as session:
            summaries = sweep_inbox(session)
        if not summaries:
            print("Inbox is empty — nothing to import.")
            return 0
        for summary in summaries:
            print(f"{summary.filename}: {summary.one_line()}")
        if any(s.rejected for s in summaries):
            print(f"Rejected records are explained in {config.REJECTS_PATH}")
        return 0

    path = Path(args.file)
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return 1
    with session_scope(factory) as session:
        summary = ingest_deposit_json(session, path.read_text(encoding="utf-8"), filename=path.name)
    print(summary.one_line())
    if summary.rejected:
        print(f"Rejected records are explained in {config.REJECTS_PATH}")
        return 2 if summary.created == 0 else 0
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .ingest import dry_run_deposit

    path = Path(args.file)
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return 1
    factory = _factory()
    with session_scope(factory) as session:
        results = dry_run_deposit(session, path.read_text(encoding="utf-8"))
    invalid = 0
    for number, outcome, detail in results:
        prefix = f"record {number}" if number else "file"
        print(f"{prefix:>10}  {outcome:<20} {detail}")
        if outcome in ("invalid", "envelope-invalid"):
            invalid += 1
    counts = {}
    for _, outcome, _ in results:
        counts[outcome] = counts.get(outcome, 0) + 1
    print("summary:", ", ".join(f"{v} {k}" for k, v in counts.items()) or "empty file")
    if invalid:
        print("Fix the invalid records before importing.", file=sys.stderr)
        return 2
    print("Looks good — deposit with: python -m crm import --inbox")
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    """Run the local enricher (public web search + scrape) for selected prospects."""
    from .enrich import enrich_prospects, write_enrich_deposit
    from .inbox import sweep_inbox
    from .models import ResearchRequest

    ids: list[int] = []
    request_id: int | None = None
    if args.ids:
        for part in str(args.ids).split(","):
            part = part.strip()
            if part:
                ids.append(int(part))
    factory = _factory()
    with session_scope(factory) as session:
        if args.request:
            req = session.get(ResearchRequest, args.request)
            if not req or req.kind != "enrich":
                print(f"No enrich research request #{args.request}", file=sys.stderr)
                return 1
            request_id = req.id
            ids = list(req.target_ids)
        if not ids:
            print("Give --ids 12,15 or --request N", file=sys.stderr)
            return 1
        print(f"Enriching {len(ids)} prospect(s) via public web search/scrape…")
        result = enrich_prospects(session, ids, request_id=request_id)
        for item in result.items:
            if item.record:
                fields = [
                    k for k in ("phone", "email", "linkedin_url", "city", "notes")
                    if k in item.record
                ]
                print(
                    f"  #{item.prospect_id} {item.full_name}: found {', '.join(fields) or 'facts'} "
                    f"({item.pages_fetched} pages)"
                )
            else:
                reason = item.shortfalls[0] if item.shortfalls else "nothing new"
                print(f"  #{item.prospect_id} {item.full_name}: {reason}")
        path = write_enrich_deposit(result)
        if path:
            print(f"Wrote deposit: {path}")
        else:
            print("No deposit written — nothing new to apply.")
            return 2 if any(i.prospect_id for i in result.items) else 1

        if args.do_import:
            summaries = sweep_inbox(session)
            for summary in summaries:
                print(f"{summary.filename}: {summary.one_line()}")
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    from .migrate_legacy import LEGACY_DB, migrate

    if not LEGACY_DB.exists():
        print(f"Legacy database not found at {LEGACY_DB}", file=sys.stderr)
        return 1
    factory = _factory()
    with session_scope(factory) as session:
        counts = migrate(session)
    print(f"Migrated {counts['companies']} companies, {counts['prospects']} prospects, "
          f"{counts['activities']} history entries into {config.DB_PATH}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    from .seed import seed, wipe

    factory = _factory()
    with session_scope(factory) as session:
        if args.wipe:
            removed = wipe(session)
            print(f"Removed {removed} seeded sample prospects.")
        else:
            created = seed(session)
            print(f"Seeded {created} clearly-fake sample prospects (wipe with: python -m crm seed --wipe)")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from .models import Company, DupeReview, Prospect, STATUSES

    factory = _factory()
    with session_scope(factory) as session:
        prospects = session.scalar(select(func.count(Prospect.id))) or 0
        companies = session.scalar(select(func.count(Company.id))) or 0
        pending_dupes = session.scalar(
            select(func.count(DupeReview.id)).where(DupeReview.status == "pending")
        ) or 0
        print(f"Database: {config.DB_PATH}")
        print(f"{prospects} prospects across {companies} companies")
        for slug, label in STATUSES.items():
            count = session.scalar(select(func.count(Prospect.id)).where(Prospect.status == slug)) or 0
            if count:
                print(f"  {label}: {count}")
        if pending_dupes:
            print(f"! {pending_dupes} near-duplicates waiting for review (Import page)")
    return 0


def cmd_backup(_args: argparse.Namespace) -> int:
    target = backup_mod.backup_now()
    print(f"Backup written: {target}")
    return 0


def cmd_init_home(args: argparse.Namespace) -> int:
    """Create a CRM_HOME folder and copy data from the repo once (safe)."""
    from .home import init_home

    home = Path(args.home).expanduser() if args.home else config.DEFAULT_LIVE_HOME
    report = init_home(home, copy_data=not args.dirs_only)
    print(f"CRM home: {report['home']}")
    for key in ("database", "photos", "rejects", "inbox_files", "backup_files"):
        if key in report:
            print(f"  {key}: {report[key]}")
    print()
    print("Start with:")
    print(f'  set CRM_HOME={report["home"]}')
    print("  python -m crm")
    print("Or double-click run-live.bat")
    return 0


def cmd_reset_new(args: argparse.Namespace) -> int:
    """Reset all prospects to New as if found today. Keeps phones/companies/research."""
    from .reset_workflow import reset_prospects_as_found_today

    if not args.yes:
        print(
            "This sets EVERY prospect to status New, clears follow-ups and last-called,\n"
            "and stamps Added = today. Contact data and enrichment are kept.\n"
            "Re-run with --yes after a backup (or let this command back up first).",
            file=sys.stderr,
        )
        return 2

    target = backup_mod.backup_now()
    print(f"Backup written: {target}")
    factory = _factory()
    with session_scope(factory) as session:
        counts = reset_prospects_as_found_today(session)
    print(
        f"Reset {counts['reset']} prospects to New (found today). "
        f"Now New: {counts['new_count']}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crm", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the web app")
    serve.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    serve.add_argument("--open", action="store_true", help="open the browser automatically")
    serve.add_argument(
        "--pick-home",
        action="store_true",
        help="show a Documents list picker before opening a database",
    )

    imp = sub.add_parser("import", help="import a prospects JSON deposit")
    imp.add_argument("file", nargs="?", help="path to a deposit .json file")
    imp.add_argument("--inbox", action="store_true", help="import every .json waiting in inbox/")

    validate = sub.add_parser("validate", help="dry-run a deposit file without importing")
    validate.add_argument("file", help="path to a deposit .json file")

    enrich = sub.add_parser(
        "enrich",
        help="run local public-web enricher for selected prospects",
    )
    enrich.add_argument("--ids", help="comma-separated prospect ids, e.g. 12,15")
    enrich.add_argument("--request", type=int, help="open enrich research request id")
    enrich.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="after writing inbox/, import waiting deposits",
    )

    sub.add_parser("migrate", help="one-time import of the legacy database")

    seed = sub.add_parser("seed", help="add clearly-fake sample data")
    seed.add_argument("--wipe", action="store_true", help="remove all seeded data")

    sub.add_parser("status", help="row counts and pending work")
    sub.add_parser("backup", help="back up the database now")

    init_home_p = sub.add_parser(
        "init-home",
        help="create CRM_HOME (data/inbox/backups) and copy from the repo once",
    )
    init_home_p.add_argument(
        "--home",
        default="",
        help=f"destination folder (default: {config.DEFAULT_LIVE_HOME})",
    )
    init_home_p.add_argument(
        "--dirs-only",
        action="store_true",
        help="create folders only; do not copy database/photos",
    )

    reset_new = sub.add_parser(
        "reset-new",
        help="set every prospect to New as if found today (keeps contact data)",
    )
    reset_new.add_argument(
        "--yes",
        action="store_true",
        help="confirm: back up, then reset all prospect workflow fields",
    )

    args = parser.parse_args(argv)
    if args.command is None:
        args.command, args.port, args.open, args.pick_home = (
            "serve", config.DEFAULT_PORT, True, False,
        )

    if args.command == "import" and not args.inbox and not args.file:
        imp.error("give a file path or --inbox")
    if args.command == "enrich" and not args.ids and not args.request:
        enrich.error("give --ids or --request")

    handlers = {
        "serve": cmd_serve, "import": cmd_import, "validate": cmd_validate,
        "enrich": cmd_enrich,
        "migrate": cmd_migrate, "seed": cmd_seed, "status": cmd_status,
        "backup": cmd_backup, "init-home": cmd_init_home, "reset-new": cmd_reset_new,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
