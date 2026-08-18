"""Orchestrate search + scrape for Stage-2 enrichment (BDR playbook).

SERP-first: read LinkedIn/city/company from search titles & snippets (like a
human scanning Google), then fetch only high-value pages. Every fact keeps
a reference URL for BDR verification.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session, joinedload

from ..models import CLOSED_STATUSES, Prospect
from .extract import (
    PageFacts,
    SourcedValue,
    company_mentioned,
    extract_from_html,
    name_mentioned,
)
from .fetch import TIMEOUT, company_seed_urls, fetch_url
from .queries import SearchAnchors, build_queries
from .search import (
    SEARCH_USER_AGENT,
    SearchDiagnostics,
    SearchHit,
    city_from_hit,
    linkedin_from_hit,
    search_many,
)
from .sources import (
    classify_url,
    city_from_directory_snippet,
    parse_bio_page,
    parse_florida_license,
    source_rank,
)

# Harvest from SERP only (login walls / PII / paywalls).
SKIP_FETCH_KINDS = frozenset({"directory", "linkedin", "people_intel"})


@dataclass
class ProspectEnrichment:
    prospect_id: int
    full_name: str
    company_name: str
    record: dict[str, Any] | None = None
    shortfalls: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    search_hits: int = 0
    search_backend: str = ""
    search_sample: list[str] = field(default_factory=list)


@dataclass
class EnrichResult:
    request_id: int | None
    items: list[ProspectEnrichment] = field(default_factory=list)

    @property
    def records(self) -> list[dict[str, Any]]:
        return [item.record for item in self.items if item.record]

    @property
    def shortfall_reasons(self) -> list[str]:
        reasons: list[str] = []
        for item in self.items:
            for reason in item.shortfalls:
                line = f"{item.full_name} (#{item.prospect_id}): {reason}"
                if line not in reasons:
                    reasons.append(line)
        return reasons[:30]


def _as_hits(urls_or_hits: list[Any]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for item in urls_or_hits:
        if isinstance(item, SearchHit):
            hits.append(item)
        elif isinstance(item, str) and item.startswith("http"):
            hits.append(SearchHit(url=item))
    return hits


def _merge_sourced(into: list[SourcedValue], more: list[SourcedValue]) -> None:
    for item in more:
        key = item.value.lower()
        if any(existing.value.lower() == key for existing in into):
            continue
        into.append(item)


def _merge_facts(into: PageFacts, more: PageFacts) -> None:
    _merge_sourced(into.phones, more.phones)
    _merge_sourced(into.emails, more.emails)
    _merge_sourced(into.linkedin_urls, more.linkedin_urls)
    _merge_sourced(into.cities, more.cities)
    _merge_sourced(into.photo_urls, more.photo_urls)
    _merge_sourced(into.notes, more.notes)


def _prefer_prepend(into: list[SourcedValue], item: SourcedValue) -> None:
    """Put high-trust SERP facts at the front so they win over random page cities."""
    key = item.value.lower()
    into[:] = [x for x in into if x.value.lower() != key]
    into.insert(0, item)


def _facts_from_serp(
    hits: list[SearchHit],
    *,
    full_name: str,
    company: str,
    company_domain: str | None,
) -> PageFacts:
    """Harvest LinkedIn, city, and call-ammo from search titles/snippets."""
    facts = PageFacts()
    last = full_name.split()[-1].lower() if full_name else ""
    company_token = (company or "").lower().split()[0] if company else ""
    directory_cities: list[SourcedValue] = []

    for hit in hits:
        kind = classify_url(hit.url)
        blob = f"{hit.title} {hit.snippet}".strip()

        li = linkedin_from_hit(hit)
        if li and (not last or last in li.lower() or last in hit.title.lower()):
            _prefer_prepend(
                facts.linkedin_urls,
                SourcedValue(li, li, "LinkedIn from search results"),
            )
            # LinkedIn title often: "Name - Title | LinkedIn"
            if " - " in hit.title and "linkedin" in hit.title.lower():
                role = hit.title.split("|")[0].strip()
                if last in role.lower() and 20 <= len(role) <= 160:
                    _merge_sourced(
                        facts.notes,
                        [SourcedValue(role, li, "title from LinkedIn search")],
                    )

        city = city_from_hit(hit)
        name_near = bool(last and (last in hit.title.lower() or last in hit.snippet.lower()))

        # Whitepages-style directories: last-resort city only.
        if kind == "directory":
            if not city:
                city = city_from_directory_snippet(blob)
            if city and name_near:
                directory_cities.append(
                    SourcedValue(city, hit.url, "city from directory snippet (unconfirmed)")
                )
            continue

        # ZoomInfo / RocketReach snippets: often "based in Winter Garden, FL"
        if kind == "people_intel":
            # Require last name + company so we don't grab a different "Wayne".
            company_near = bool(company_token and company_token in blob.lower())
            if city and name_near and company_near:
                _prefer_prepend(
                    facts.cities,
                    SourcedValue(city, hit.url, "city from people-intel search snippet"),
                )
            if blob and name_near and company_near and 40 <= len(blob) <= 280:
                _merge_sourced(
                    facts.notes,
                    [SourcedValue(blob[:280], hit.url, "people-intel snippet")],
                )
            continue

        # License SERP often shows a mailing/office city — backup only.
        if kind == "license":
            if city and name_near and not facts.cities:
                _merge_sourced(
                    facts.cities,
                    [SourcedValue(city, hit.url, "city from license search snippet")],
                )
        elif city and (name_near or "linkedin.com/in/" in hit.url.lower()):
            trust = "linkedin.com/in/" in hit.url.lower() or "based in" in blob.lower()
            note = (
                "city from LinkedIn Location"
                if "linkedin.com/in/" in hit.url.lower()
                else "city from search snippet"
            )
            sv = SourcedValue(city, hit.url, note)
            if trust:
                _prefer_prepend(facts.cities, sv)
            else:
                _merge_sourced(facts.cities, [sv])

        if company_domain and company_domain.lower() in hit.url.lower():
            if blob and last and last in blob.lower() and 40 <= len(blob) <= 280:
                _merge_sourced(
                    facts.notes,
                    [SourcedValue(blob[:280], hit.url, "company hit snippet")],
                )

        if kind == "license" and blob and last in blob.lower():
            _merge_sourced(
                facts.notes,
                [SourcedValue(f"License search: {blob[:280]}", hit.url, "license SERP")],
            )

        if kind in {"bio", "news"} and last and company_token:
            if last in blob.lower() and company_token in blob.lower() and len(blob) >= 40:
                _merge_sourced(
                    facts.notes,
                    [SourcedValue(blob[:280], hit.url, "bio/news snippet")],
                )

    if not facts.cities and directory_cities:
        _merge_sourced(facts.cities, directory_cities[:1])

    return facts


def _apply_license(facts: PageFacts, parsed: dict) -> None:
    url = parsed.get("source_url") or ""
    # License mailing city is useful backup only — LinkedIn "Location:" wins for cold calls.
    if parsed.get("city") and not facts.cities:
        _merge_sourced(
            facts.cities,
            [SourcedValue(parsed["city"], url, "city from FL license record")],
        )
    if parsed.get("note"):
        _merge_sourced(
            facts.notes,
            [SourcedValue(parsed["note"], url, "FL license / DBPR")],
        )


def _apply_bio(facts: PageFacts, parsed: dict) -> None:
    url = parsed.get("source_url") or ""
    if parsed.get("note"):
        _merge_sourced(
            facts.notes,
            [SourcedValue(parsed["note"], url, "bio / about page")],
        )


def _best_city(cities: list[SourcedValue]) -> SourcedValue | None:
    """Prefer LinkedIn Location / people-intel over license mailing / directories."""
    if not cities:
        return None

    def score(item: SourcedValue) -> int:
        note = (item.note or "").lower()
        url = (item.source_url or "").lower()
        if "linkedin" in note or "linkedin.com/in/" in url:
            return 100
        if "people-intel" in note:
            return 90
        if "based in" in note or "search snippet" in note:
            return 70
        if "license" in note:
            return 40
        if "directory" in note:
            return 10
        return 50

    return max(cities, key=score)


def _build_record(prospect: Prospect, facts: PageFacts) -> dict[str, Any] | None:
    """Build a Stage-2 deposit; every included field has a verify URL in evidence."""
    evidence = facts.evidence_rows()
    payload: dict[str, Any] = {
        "prospect_id": prospect.id,
        "full_name": prospect.full_name,
        "company": {
            "name": prospect.company.name,
            "domain": prospect.company.domain,
            "website": prospect.company.website,
            "region": prospect.company.region,
        },
        "evidence": evidence[:12],
    }
    changed = False

    def _pick(items: list[SourcedValue]) -> SourcedValue | None:
        return items[0] if items else None

    if not prospect.phone:
        phone = _pick(facts.phones)
        if phone:
            payload["phone"] = phone.value
            changed = True
    if not prospect.email:
        domain = (prospect.company.domain or "").lower()
        pick = _pick(facts.emails)
        if domain and facts.emails:
            for email in facts.emails:
                low = email.value.lower()
                if low.endswith("@" + domain) or low.endswith("." + domain):
                    pick = email
                    break
        if pick:
            payload["email"] = pick.value
            changed = True
    if not prospect.linkedin_url:
        li = _pick(facts.linkedin_urls)
        if li:
            payload["linkedin_url"] = li.value
            changed = True
    if not prospect.city:
        city = _best_city(facts.cities)
        if city:
            raw = city.value
            payload["city"] = raw.split(",")[0].strip() if "," in raw else raw
            # Title-case mailing-style ALL CAPS cities
            if payload["city"].isupper():
                payload["city"] = payload["city"].title()
            changed = True
    if not prospect.photo_path:
        photo = _pick(facts.photo_urls)
        if photo:
            payload["photo_url"] = photo.value
            changed = True
    if facts.notes:
        # Only deposit notes when we also got a core field, OR notes alone if nothing else known
        payload["notes"] = " | ".join(n.value for n in facts.notes[:2])
        changed = True

    if not changed or not evidence:
        return None
    return payload


def _order_fetch_urls(
    seed: list[str],
    hits: list[SearchHit],
    *,
    company_domain: str | None,
) -> list[str]:
    """Fetch license/bio/company pages; skip LinkedIn + people directories."""
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if not url or url in seen:
            return
        kind = classify_url(url)
        if kind in SKIP_FETCH_KINDS:
            return
        rank = source_rank(url)
        if company_domain and company_domain.lower() in urlparse(url).netloc.lower():
            rank = min(rank, 4)
        seen.add(url)
        scored.append((rank, url))

    for url in seed:
        add(url)
    for hit in hits:
        add(hit.url)

    scored.sort(key=lambda pair: (pair[0], pair[1]))
    return [url for _, url in scored]


def enrich_one(
    prospect: Prospect,
    *,
    client: httpx.Client,
) -> ProspectEnrichment:
    company = prospect.company
    anchors = SearchAnchors(
        full_name=prospect.full_name,
        company=company.name,
        city=prospect.city,
        region=prospect.region or company.region,
        domain=company.domain,
    )
    item = ProspectEnrichment(
        prospect_id=prospect.id,
        full_name=prospect.full_name,
        company_name=company.name,
    )
    queries = build_queries(anchors)
    seed = company_seed_urls(company.website, company.domain)
    diag = SearchDiagnostics()
    hits = _as_hits(
        search_many(
            queries,
            client=client,
            max_urls=18,
            full_name=prospect.full_name,
            diagnostics=diag,
        )
    )
    item.search_hits = diag.hits
    item.search_backend = diag.backend
    item.search_sample = list(diag.sample_urls)

    merged = _facts_from_serp(
        hits,
        full_name=prospect.full_name,
        company=company.name,
        company_domain=company.domain,
    )
    # If SERP already gave LinkedIn + city, still fetch license/bio for notes — fewer pages
    has_identity = bool(merged.linkedin_urls) and (
        bool(merged.cities) or bool(prospect.city)
    )
    ordered = _order_fetch_urls(seed, hits, company_domain=company.domain)
    fetch_budget = 8 if has_identity else 14

    for url in ordered[:fetch_budget]:
        page = fetch_url(url, client=client)
        item.pages_fetched += 1
        if not page.ok or not page.text:
            continue

        kind = classify_url(page.final_url)
        page_facts = PageFacts()

        if kind == "license":
            lic = parse_florida_license(page.text, page.final_url, prospect.full_name)
            if lic:
                _apply_license(page_facts, lic)
        else:
            page_facts = extract_from_html(
                page.text,
                page_url=page.final_url,
                full_name=prospect.full_name,
                company=company.name,
                city=prospect.city or (merged.cities[0].value if merged.cities else None),
            )
            if kind == "bio" or "/about" in page.final_url.lower():
                bio = parse_bio_page(
                    page.text, page.final_url, prospect.full_name, company.name
                )
                if bio:
                    _apply_bio(page_facts, bio)

        # Don't let random site/footer cities override LinkedIn / SERP city.
        if merged.cities and page_facts.cities and kind not in {"license", "bio"}:
            page_facts.cities = []

        useful = page_facts.has_facts()
        text_ok = name_mentioned(page.text, prospect.full_name) or company_mentioned(
            page.text, company.name
        )
        if useful and (text_ok or url in seed or kind == "license"):
            _merge_facts(merged, page_facts)

    record = _build_record(prospect, merged)
    if record:
        item.record = record
    else:
        if diag.error:
            item.shortfalls.append(diag.error)
        elif diag.hits == 0:
            item.shortfalls.append("web search returned no results")
        else:
            missing = []
            if not prospect.phone and not merged.phones:
                missing.append("direct phone")
            if not prospect.linkedin_url and not merged.linkedin_urls:
                missing.append("LinkedIn")
            if not prospect.city and not merged.cities:
                missing.append("city")
            already = []
            if prospect.linkedin_url:
                already.append("LinkedIn known")
            if prospect.city:
                already.append("city known")
            if prospect.phone:
                already.append("phone known")
            msg = "no new public facts found"
            if missing:
                msg += f" (still missing: {', '.join(missing)})"
            if already and not missing:
                msg += f" (already had: {', '.join(already)})"
            if item.search_sample:
                msg += f" · saw {diag.hits} search hits via {diag.backend or '?'}"
            item.shortfalls.append(msg)
    return item


def load_prospects(session: Session, ids: list[int]) -> list[Prospect]:
    from sqlalchemy import select

    prospects: list[Prospect] = []
    for pid in ids:
        prospect = session.scalar(
            select(Prospect)
            .where(Prospect.id == pid)
            .options(joinedload(Prospect.company))
        )
        if prospect and prospect.status not in CLOSED_STATUSES:
            prospects.append(prospect)
    return prospects


def enrich_prospects(
    session: Session,
    ids: list[int],
    *,
    request_id: int | None = None,
    client: httpx.Client | None = None,
) -> EnrichResult:
    """Run the enricher for the given prospect ids."""
    targets = load_prospects(session, ids)
    result = EnrichResult(request_id=request_id)
    owns = client is None
    # Browser-like UA so search engines don't treat us as a hard bot.
    client = client or httpx.Client(
        headers={
            "User-Agent": SEARCH_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    try:
        for i, prospect in enumerate(targets):
            if i:
                time.sleep(0.8)  # be polite between prospects
            result.items.append(enrich_one(prospect, client=client))
    finally:
        if owns:
            client.close()
    if not targets:
        result.items.append(
            ProspectEnrichment(
                prospect_id=0,
                full_name="(none)",
                company_name="",
                shortfalls=["no open prospects matched the given ids"],
            )
        )
    return result
