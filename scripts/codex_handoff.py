"""Queue inspection and result ingestion for the subscription-driven Codex worker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prospecting.codex_handoff import (
    get_run,
    handoff_prompt,
    ingest_codex_results,
    job_path,
    list_queued_runs,
    queue_gap_fill_run,
    result_path,
    validate_result_file,
)
from prospecting.config import get_settings
from prospecting.database import build_session_factory, create_db_engine, initialize_database


def factory():
    settings = get_settings()
    engine = create_db_engine(settings)
    initialize_database(engine)
    return settings, build_session_factory(engine)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List research runs awaiting a Codex worker")
    show = commands.add_parser("show", help="Show the job JSON and suggested Codex prompt")
    show.add_argument("--run-id", required=True)
    validate = commands.add_parser("validate", help="Validate a Codex result JSON without writing to SQLite")
    validate.add_argument("--run-id", required=True)
    validate.add_argument("--file", type=Path)
    ingest = commands.add_parser("ingest", help="Persist a validated Codex result JSON")
    ingest.add_argument("--run-id", required=True)
    ingest.add_argument("--file", type=Path)
    top_up = commands.add_parser("top-up", help="Queue a deduplicated follow-up for a completed shortfall")
    top_up.add_argument("--run-id", required=True)
    args = parser.parse_args()
    settings, session_factory = factory()

    if args.command == "list":
        for run in list_queued_runs(session_factory):
            print(f"{run.id} | {run.created_at.isoformat()} | {run.icp_json.get('industry', 'Unknown industry')}")
        return
    if args.command == "show":
        run = get_run(session_factory, args.run_id)
        if not job_path(run.id).exists():
            raise SystemExit(f"Job file is missing: {job_path(run.id)}")
        print(job_path(run.id).read_text(encoding="utf-8"))
        print("\nSuggested Codex prompt:\n" + handoff_prompt(run.id))
        return
    if args.command == "top-up":
        new_run_id = queue_gap_fill_run(session_factory, settings, args.run_id)
        print(f"Queued gap-filling run: {new_run_id}")
        print("\nSuggested Codex prompt:\n" + handoff_prompt(new_run_id))
        return
    file_path = args.file or result_path(args.run_id)
    if args.command == "validate":
        result = validate_result_file(args.run_id, file_path)
        print(json.dumps({"run_id": result.run_id, "accounts": len(result.accounts), "valid": True}, indent=2))
        return
    result = ingest_codex_results(session_factory, settings, args.run_id, file_path)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
