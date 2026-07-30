"""Query approved, unsuppressed prospects above the high-confidence threshold."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from prospecting.config import get_settings
from prospecting.database import build_session_factory, create_db_engine, initialize_database, session_scope
from prospecting.models import Company, Prospect, ResearchRunProspect
from prospecting.review import is_suppressed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Research run UUID to query")
    parser.add_argument("--threshold", type=float, default=0.85)
    args = parser.parse_args()

    engine = create_db_engine(get_settings())
    initialize_database(engine)
    factory = build_session_factory(engine)
    with session_scope(factory) as session:
        rows = session.execute(
            select(Prospect, Company, ResearchRunProspect)
            .join(Company, Prospect.company_id == Company.id)
            .join(ResearchRunProspect, ResearchRunProspect.prospect_id == Prospect.id)
            .where(
                ResearchRunProspect.research_run_id == args.run_id,
                Prospect.confidence_score > args.threshold,
                Prospect.review_status == "approved",
                ResearchRunProspect.is_suppressed.is_(False),
            )
            .order_by(ResearchRunProspect.icp_alignment_score.desc(), Prospect.confidence_score.desc())
        ).all()
        rows = [row for row in rows if not is_suppressed(session, row[0])]
    if not rows:
        print("No approved prospects meet the selected threshold.")
        return
    print("Company | Prospect | Role | Confidence | ICP alignment | Owner")
    for prospect, company, link in rows:
        print(f"{company.name} | {prospect.full_name} | {prospect.role} | {prospect.confidence_score:.2f} | {link.icp_alignment_score:.2f} | {prospect.owner or ''}")


if __name__ == "__main__":
    main()

