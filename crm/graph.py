"""Build BDR relationship maps from CRM data (no live web calls).

Company-centered spider payloads with buying-role hints and coverage gaps —
the view BDRs actually use for multi-threading an account.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .models import CLOSED_STATUSES, Company, Prospect, STATUSES

ATLAS_COMPANY_CAP = 80

# Title → buying-role hints (not formal org chart; enough for cold-call prep).
_ECONOMIC = re.compile(
    r"\b(ceo|owner|founder|president|division\s+president|managing\s+partner|"
    r"principal|evp|svp|vp|vice\s+president|chief)\b",
    re.I,
)
_INFLUENCER = re.compile(
    r"\b(director|head\s+of|senior\s+manager|sr\.?\s+manager|acquisitions?)\b",
    re.I,
)
_SPECIALIST = re.compile(
    r"\b(land|entitlement|development|acquisition|operations|product|sales|"
    r"engineering|clinical|practice)\b",
    re.I,
)


def infer_buying_role(title: str | None) -> dict[str, str]:
    """Return {slug, label} for BDR color-coding."""
    t = (title or "").strip()
    if not t:
        return {"slug": "unknown", "label": "Unknown role"}
    if _ECONOMIC.search(t):
        return {"slug": "economic_buyer", "label": "Economic buyer"}
    if _INFLUENCER.search(t):
        return {"slug": "influencer", "label": "Influencer"}
    if _SPECIALIST.search(t):
        return {"slug": "coach", "label": "Specialist / coach"}
    return {"slug": "contact", "label": "Contact"}


def _title_tokens(titles: list[str] | None) -> list[str]:
    """Significant words from ICP target titles for gap matching."""
    stop = {
        "of", "the", "and", "or", "a", "an", "to", "for", "in", "vp", "vice",
        "president", "senior", "sr", "jr", "manager", "director", "head",
    }
    tokens: list[str] = []
    for title in titles or []:
        for word in re.findall(r"[a-z0-9]+", title.lower()):
            if len(word) < 3 or word in stop:
                continue
            if word not in tokens:
                tokens.append(word)
    return tokens


def title_matches_icp(title: str | None, target_titles: list[str] | None) -> bool:
    """True if the contact title looks like an ICP target (token overlap)."""
    blob = (title or "").lower()
    if not blob:
        return False
    for exact in target_titles or []:
        if exact.strip() and exact.strip().lower() in blob:
            return True
    tokens = _title_tokens(target_titles)
    if not tokens:
        return False
    return any(tok in blob for tok in tokens)


def _region_of(prospect: Prospect) -> str | None:
    return (prospect.region or (prospect.company.region if prospect.company else None) or "").strip() or None


def _host_from_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    try:
        host = urlparse(url.strip()).netloc.lower()
    except Exception:
        return None
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else None


def coverage_gaps(
    prospects: list[Prospect],
    *,
    focus_id: int | None = None,
    target_titles: list[str] | None = None,
) -> list[dict[str, str]]:
    """Actionable gaps a BDR can fix before / during outreach."""
    open_p = [p for p in prospects if p.status not in CLOSED_STATUSES]
    gaps: list[dict[str, str]] = []

    if len(open_p) <= 1:
        gaps.append({
            "code": "single_threaded",
            "severity": "high",
            "text": "Only one open contact — multi-thread this account (find a second title).",
        })

    roles = [infer_buying_role(p.title)["slug"] for p in open_p]
    if open_p and "economic_buyer" not in roles:
        gaps.append({
            "code": "no_economic_buyer",
            "severity": "high",
            "text": "No VP / President / owner title on file — hunt the economic buyer.",
        })

    if target_titles and open_p and not any(
        title_matches_icp(p.title, target_titles) for p in open_p
    ):
        sample = target_titles[0]
        gaps.append({
            "code": "no_target_title",
            "severity": "medium",
            "text": f"No contact matching ICP titles (e.g. {sample}) yet.",
        })

    if open_p and not any(p.phone for p in open_p):
        gaps.append({
            "code": "no_phone",
            "severity": "high",
            "text": "No direct phone on any contact — run enricher or check the company site.",
        })

    if open_p and not any(p.linkedin_url for p in open_p):
        gaps.append({
            "code": "no_linkedin",
            "severity": "medium",
            "text": "No LinkedIn URLs — harder to verify title and city before calling.",
        })

    if open_p and not any((p.city or "").strip() for p in open_p):
        gaps.append({
            "code": "no_city",
            "severity": "medium",
            "text": "No city on file — lock geography before personal outreach.",
        })

    focus = next((p for p in prospects if focus_id and p.id == focus_id), None)
    if focus and not focus.phone:
        gaps.append({
            "code": "focus_no_phone",
            "severity": "high",
            "text": f"{focus.full_name} (focus) still needs a phone for the dial.",
        })

    # De-dupe by code keeping first
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for g in gaps:
        if g["code"] in seen:
            continue
        seen.add(g["code"])
        out.append(g)
    return out


def _person_card(prospect: Prospect, *, focus: bool = False) -> dict[str, Any]:
    role = infer_buying_role(prospect.title)
    return {
        "id": f"person:{prospect.id}",
        "type": "person",
        "label": prospect.full_name,
        "prospect_id": prospect.id,
        "title": prospect.title,
        "city": (prospect.city or "").strip() or None,
        "region": _region_of(prospect),
        "status": prospect.status,
        "status_label": STATUSES.get(prospect.status, prospect.status),
        "icp_score": prospect.icp_score,
        "phone": bool(prospect.phone),
        "email": bool(prospect.email),
        "linkedin_url": prospect.linkedin_url,
        "has_photo": bool(prospect.photo_path),
        "photo_url": f"/photos/{prospect.id}" if prospect.photo_path else None,
        "role": role["slug"],
        "role_label": role["label"],
        "focus": focus,
        "href": f"/prospects/{prospect.id}",
        "closed": prospect.status in CLOSED_STATUSES,
    }


def build_company_graph(
    session: Session,
    company_id: int,
    *,
    focus_prospect_id: int | None = None,
    target_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Company-centered spider: people as cards + coverage gaps for BDRs."""
    company = session.get(Company, company_id)
    if not company:
        return {
            "layout": "spider",
            "company": None,
            "people": [],
            "gaps": [],
            "focus_id": None,
            "meta": {"empty": True, "reason": "company not found", "scope": "company"},
        }

    prospects = list(
        session.scalars(
            select(Prospect)
            .where(Prospect.company_id == company_id)
            .options(joinedload(Prospect.company))
            .order_by(Prospect.icp_score.desc().nullslast(), Prospect.full_name)
        )
    )

    people = [
        _person_card(p, focus=bool(focus_prospect_id and p.id == focus_prospect_id))
        for p in prospects
    ]
    # Sort: focus first, then economic buyers, then ICP
    role_rank = {"economic_buyer": 0, "influencer": 1, "coach": 2, "contact": 3, "unknown": 4}
    people.sort(
        key=lambda c: (
            0 if c.get("focus") else 1,
            role_rank.get(c.get("role") or "unknown", 9),
            -(c.get("icp_score") or 0),
            c.get("label") or "",
        )
    )

    gaps = coverage_gaps(
        prospects, focus_id=focus_prospect_id, target_titles=target_titles
    )
    cities = sorted({c["city"] for c in people if c.get("city")})
    regions = sorted({c["region"] for c in people if c.get("region")})
    if company.region and company.region.strip() and company.region.strip() not in regions:
        regions.insert(0, company.region.strip())

    # Legacy nodes/edges kept so older clients / tests can still read a graph shape
    nodes = [
        {
            "id": f"company:{company.id}",
            "type": "company",
            "label": company.name,
            "company_id": company.id,
            "domain": company.domain,
            "region": company.region,
        }
    ]
    edges = []
    for person in people:
        nodes.append({**person})
        edges.append({"source": person["id"], "target": f"company:{company.id}", "type": "works_at"})

    focus_id = f"person:{focus_prospect_id}" if focus_prospect_id else f"company:{company.id}"

    return {
        "layout": "spider",
        "company": {
            "id": company.id,
            "name": company.name,
            "domain": company.domain,
            "website": company.website,
            "region": company.region,
            "industry": company.industry,
            "size_band": company.size_band,
        },
        "people": people,
        "cities": cities,
        "regions": regions,
        "gaps": gaps,
        "nodes": nodes,
        "edges": edges,
        "focus_id": focus_id if any(n["id"] == focus_id for n in nodes) else f"company:{company.id}",
        "meta": {
            "scope": "company",
            "company_id": company.id,
            "company_name": company.name,
            "person_count": len(people),
            "open_count": sum(1 for p in people if not p.get("closed")),
            "empty": len(people) == 0,
        },
    }


def build_atlas_graph(
    session: Session,
    *,
    region: str | None = None,
    status: str | None = None,
    include_closed: bool = False,
    max_companies: int = ATLAS_COMPANY_CAP,
    target_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Atlas = list of company spiders (summaries), not one noisy mega-graph."""
    stmt = (
        select(Company)
        .options(joinedload(Company.prospects))
        .order_by(Company.name)
    )
    companies = list(session.scalars(stmt).unique())

    accounts: list[dict[str, Any]] = []
    for company in companies:
        prospects = list(company.prospects or [])
        if status:
            prospects = [p for p in prospects if p.status == status]
        elif not include_closed:
            prospects = [p for p in prospects if p.status not in CLOSED_STATUSES]

        if region:
            region_l = region.strip().lower()
            prospects = [
                p
                for p in prospects
                if (p.region or "").lower() == region_l
                or (company.region or "").lower() == region_l
            ]
            if not prospects and (company.region or "").lower() != region_l:
                continue

        if not prospects:
            continue

        gaps = coverage_gaps(prospects, target_titles=target_titles)
        people_preview = [
            {
                "id": p.id,
                "name": p.full_name,
                "title": p.title,
                "role": infer_buying_role(p.title)["slug"],
                "href": f"/prospects/{p.id}",
            }
            for p in sorted(
                prospects,
                key=lambda x: (-(x.icp_score or 0), x.full_name),
            )[:5]
        ]
        accounts.append({
            "company_id": company.id,
            "name": company.name,
            "domain": company.domain,
            "region": company.region,
            "person_count": len(prospects),
            "gap_count": len(gaps),
            "top_gap": gaps[0]["text"] if gaps else None,
            "gaps": gaps[:4],
            "people": people_preview,
            "href": f"/prospects/{people_preview[0]['id']}" if people_preview else None,
            "graph_href": f"/api/graph?company_id={company.id}",
        })
        if len(accounts) >= max_companies:
            break

    # Prefer accounts with gaps / more people for BDR attention
    accounts.sort(key=lambda a: (-a["gap_count"], -a["person_count"], a["name"]))

    # Flat nodes/edges for backward-compatible tests (people + companies only)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for acct in accounts:
        co_id = f"company:{acct['company_id']}"
        nodes.append({
            "id": co_id,
            "type": "company",
            "label": acct["name"],
            "company_id": acct["company_id"],
            "region": acct.get("region"),
        })
        for person in acct["people"]:
            pid = f"person:{person['id']}"
            nodes.append({
                "id": pid,
                "type": "person",
                "label": person["name"],
                "prospect_id": person["id"],
                "title": person.get("title"),
                "role": person.get("role"),
                "href": person.get("href"),
                "region": acct.get("region"),
            })
            edges.append({"source": pid, "target": co_id, "type": "works_at"})

    return {
        "layout": "atlas",
        "accounts": accounts,
        "nodes": nodes,
        "edges": edges,
        "focus_id": None,
        "meta": {
            "scope": "atlas",
            "region": region,
            "status": status,
            "account_count": len(accounts),
            "node_count": len(nodes),
            "truncated": len(accounts) >= max_companies,
            "empty": len(accounts) == 0,
        },
    }
