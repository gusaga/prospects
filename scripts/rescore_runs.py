"""Recalculate stored prospect alignment after changing local scoring rules."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prospecting.config import get_settings
from prospecting.database import build_session_factory, create_db_engine, initialize_database
from prospecting.rescoring import rescore_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Finished research run UUID to recalculate")
    args = parser.parse_args()
    settings = get_settings()
    engine = create_db_engine(settings)
    initialize_database(engine)
    result = rescore_run(build_session_factory(engine), settings, args.run_id)
    print(
        f"Rescored {result.run_id}: {result.status}; "
        f"{result.metrics['qualified_prospects']} qualified prospects from "
        f"{result.metrics['contacts_discovered']} discovered."
    )
    for reason in result.metrics.get("shortfall_reasons", []):
        print(f"- {reason.encode('ascii', 'replace').decode('ascii')}")


if __name__ == "__main__":
    main()
