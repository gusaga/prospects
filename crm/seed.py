"""Clearly-fake sample data behind `python -m crm seed`; wiped with --wipe.

Every seeded row has source='seed', fictional 555 phone numbers, and
.example domains so it can never be mistaken for a real prospect.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .dedupe import name_key
from .models import Activity, Company, Prospect

_COMPANIES = [
    ("Sunrise Example Communities", "sunrise-communities.example", "Land development", "11-50", "Texas"),
    ("Bluebonnet Demo Builders", "bluebonnet-builders.example", "Homebuilding", "11-50", "Texas"),
    ("Saguaro Sample Development", "saguaro-dev.example", "Land development", "11-50", "Arizona"),
    ("Gulfshore Placeholder Homes", "gulfshore-homes.example", "Homebuilding", "51-100", "Florida"),
    ("Peach State Test Partners", "peachstate-partners.example", "Land development", "11-50", "Georgia"),
    ("Lone Star Mock Land Co", "lonestar-land.example", "Land development", "2-10", "Texas"),
    ("Sandia Fake Estates", "sandia-estates.example", "Master developer", "11-50", "New Mexico"),
    ("Palmetto Dummy Development", "palmetto-dev.example", "Land development", "11-50", "South Carolina"),
]

_PEOPLE = [
    # (name, title, company idx, status, priority, score, days_to_followup or None, phone?, email?)
    ("Sam Sample", "VP of Land Development", 0, "queued", 3, 92, None, True, True),
    ("Dana Demo", "Senior Land Development Manager", 0, "new", 2, 84, None, True, False),
    ("Pat Placeholder", "Division President", 1, "queued", 3, 88, None, True, True),
    ("Fay Fictional", "VP of Acquisitions", 1, "follow_up", 2, 79, 0, False, True),
    ("Mock Morrison", "VP of Land Development", 2, "follow_up", 3, 90, 0, True, True),
    ("Terry Testcase", "Land Development Manager", 2, "new", 1, 61, None, False, False),
    ("Gene Generated", "VP of Land Development", 3, "no_answer", 2, 76, 1, True, False),
    ("Ida Imaginary", "Director of Land Acquisition", 3, "queued", 2, 71, None, True, True),
    ("Bob Bogus", "Division President", 4, "conversation", 3, 86, 2, True, True),
    ("Nina Notreal", "VP of Land Development", 4, "new", 2, 82, None, False, True),
    ("Stan Standin", "VP of Development", 5, "queued", 1, 58, None, True, False),
    ("Molly Mockup", "Owner", 5, "new", 2, 66, None, True, True),
    ("Phil Phony", "VP of Land Development", 6, "follow_up", 2, 74, 3, True, True),
    ("Eve Example", "Entitlements Manager", 6, "new", 1, 52, None, False, False),
    ("Dean Dummy", "VP of Land Development", 7, "meeting", 3, 95, 5, True, True),
    ("Tess Trial", "VP of Acquisitions", 7, "new", 2, 68, None, True, False),
    ("Sal Specimen", "Senior Land Development Manager", 0, "no_answer", 2, 73, 0, True, False),
    ("Rita Rehearsal", "Community Development Director", 1, "not_fit", 1, 34, None, False, False),
    ("Max Makebelieve", "VP of Land Development", 2, "queued", 3, 91, None, True, True),
    ("Lila Lorem", "Land Acquisition Manager", 3, "new", 2, 64, None, False, True),
    ("Ivan Ipsum", "Division President", 4, "queued", 2, 77, None, True, False),
    ("Wendy Wireframe", "VP of Land Development", 5, "follow_up", 3, 85, -1, True, True),
    ("Hank Hypothetical", "Chief Development Officer", 6, "new", 2, 70, None, True, False),
    ("Carla Concept", "VP of Land Development", 7, "do_not_contact", 1, 45, None, False, False),
    ("Pete Prototype", "VP of Land Development", 0, "conversation", 3, 89, 1, True, True),
]


def seed(session: Session) -> int:
    companies: list[Company] = []
    for name, domain, industry, size, region in _COMPANIES:
        existing = session.scalar(select(Company).where(Company.domain == domain))
        if existing:
            companies.append(existing)
            continue
        company = Company(
            name=name, domain=domain, website=f"https://{domain}",
            industry=industry, size_band=size, region=region,
        )
        session.add(company)
        session.flush()
        companies.append(company)

    created = 0
    for i, (name, title, company_idx, status, priority, score, followup_days, has_phone, has_email) in enumerate(_PEOPLE):
        company = companies[company_idx]
        if session.scalar(select(Prospect).where(
            Prospect.company_id == company.id, Prospect.name_key == name_key(name)
        )):
            continue
        first = name.split()[0].lower()
        prospect = Prospect(
            company_id=company.id,
            full_name=name,
            name_key=name_key(name),
            title=title,
            phone=f"(512) 555-01{i:02d}" if has_phone else None,
            email=f"{first}@{company.domain}" if has_email else None,
            linkedin_url=f"https://www.linkedin.com/in/{first}-example-{i}" if i % 3 == 0 else None,
            region=company.region,
            icp_score=score,
            icp_rationale=f"SAMPLE DATA — {title} at a {company.industry.lower()} firm in {company.region}",
            evidence=[{"url": f"https://{company.domain}/team", "note": "team page (fake)"}],
            status=status,
            priority=priority,
            notes="Seeded sample record. Wipe with: python -m crm seed --wipe",
            source="seed",
            next_followup_on=date.today() + timedelta(days=followup_days) if followup_days is not None else None,
        )
        session.add(prospect)
        session.flush()
        session.add(Activity(prospect_id=prospect.id, kind="system", body="Seeded sample prospect"))
        created += 1
    return created


def wipe(session: Session) -> int:
    """Remove seeded prospects, their activity, and companies left empty."""
    seeded = list(session.scalars(select(Prospect).where(Prospect.source == "seed")))
    company_ids = {p.company_id for p in seeded}
    for prospect in seeded:
        session.delete(prospect)  # cascades to activities
    session.flush()
    removed = len(seeded)
    for company_id in company_ids:
        company = session.get(Company, company_id)
        if company and not company.prospects:
            session.delete(company)
    return removed
