"""Tests for the local enricher (mocked HTTP — no live web)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from crm.db import initialize
from crm.enrich.deposit import write_enrich_deposit
from crm.enrich.engine import EnrichResult, enrich_one, enrich_prospects
from crm.enrich.extract import extract_from_html
from crm.enrich.fetch import FetchedPage
from crm.enrich.queries import SearchAnchors, build_queries
from crm.enrich.search import SearchHit, city_from_hit, parse_ddg_html
from crm.enrich.sources import (
    classify_url,
    parse_bio_page,
    parse_florida_license,
)
from crm.ingest import ingest_records
from crm.models import Prospect


CONTACT_HTML = """
<html><body>
  <h1>Hill Country Communities — Team</h1>
  <p>Jordan Rivera, VP of Land Development, based in Austin, TX.</p>
  <p>Call us at (512) 555-0134 or email jrivera@hillcountrycommunities.example</p>
  <a href="https://www.linkedin.com/in/jordan-rivera-land">LinkedIn</a>
  <p>Jordan Rivera announced a new 400-lot community project in 2026.</p>
</body></html>
"""

EMPTY_HTML = """
<html><body><h1>Unrelated blog</h1><p>No useful contact info here.</p></body></html>
"""

LICENSE_HTML = """
<html><body>
  <h1>Licensee Details</h1>
  <p>Name: WAYNE BROEDEL</p>
  <p>Main Address: 123 Main St Apopka, Florida 32712</p>
  <p>License Number: CBC1234567</p>
  <p>License Type: Certified Building Contractor</p>
  <p>DBA Name: Maronda Homes</p>
</body></html>
"""

BIO_HTML = """
<html><body>
  <h1>About Our Board</h1>
  <p>Wayne Broedel serves as Division President for Maronda Homes. Over his career
  he has delivered more than 10,000 homes across Central Florida counties and partners
  with Hero Homes community programs.</p>
</body></html>
"""

DDG_HTML = """
<html><body>
  <div class="result">
    <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fwayne-broedel-b3196229%2F">
      Wayne Broedel - Division President - Maronda Homes | LinkedIn
    </a>
    <a class="result__snippet">Winter Garden, Florida · Experience: Maronda Homes · Education: Florida State University</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fhillcountrycommunities.example%2Fteam">Team</a>
    <a class="result__snippet">Jordan Rivera leads land development in Austin, TX.</a>
  </div>
  <a href="https://news.example/article">News</a>
</body></html>
"""


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr("crm.config.INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr("crm.config.PROCESSED_DIR", tmp_path / "inbox" / "processed")
    monkeypatch.setattr("crm.config.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("crm.config.REJECTS_PATH", tmp_path / "data" / "rejects.jsonl")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "data").mkdir()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    initialize(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Session(engine) as sess:
        yield sess


def _seed_prospect(session: Session, **overrides) -> Prospect:
    base = {
        "company": {
            "name": "Hill Country Communities",
            "domain": "hillcountrycommunities.example",
            "website": "https://hillcountrycommunities.example",
            "region": "Texas",
        },
        "full_name": "Jordan Rivera",
        "title": "VP of Land Development",
        "icp_score": 90,
        "icp_rationale": "Exact fit",
        "evidence": [
            {"url": "https://hillcountrycommunities.example/team", "note": "team page"}
        ],
        "status": "queued",
    }
    base.update(overrides)
    ingest_records(session, [base], filename="seed.json", source="codex")
    return session.scalar(select(Prospect))


def test_build_queries_name_company_first():
    qs = build_queries(
        SearchAnchors(
            full_name="Jordan Rivera",
            company="Hill Country Communities",
            city="Austin",
            region="Texas",
            domain="hillcountrycommunities.example",
        )
    )
    assert qs
    assert qs[0] == "Jordan Rivera Hill Country Communities"
    assert any("linkedin" in q.lower() for q in qs)
    assert any("Austin" in q for q in qs)
    assert all("Jordan Rivera" in q for q in qs)


def test_build_queries_florida_adds_license_search():
    qs = build_queries(
        SearchAnchors(
            full_name="Wayne Broedel",
            company="Maronda Homes",
            city="Winter Garden",
            region="Florida",
        )
    )
    assert any("myfloridalicense" in q.lower() for q in qs)


def test_extract_finds_phone_email_linkedin_city_with_source_urls():
    page = "https://hillcountrycommunities.example/team"
    facts = extract_from_html(
        CONTACT_HTML,
        page_url=page,
        full_name="Jordan Rivera",
        company="Hill Country Communities",
        city=None,
    )
    assert facts.phones and facts.phones[0].value.startswith("(512)")
    assert facts.phones[0].source_url == page
    assert any("jrivera@" in e.value for e in facts.emails)
    assert facts.linkedin_urls
    assert all(e.get("url") for e in facts.evidence_rows())


def test_extract_ignores_unrelated_page_without_anchors():
    facts = extract_from_html(
        EMPTY_HTML,
        page_url="https://random.example/post",
        full_name="Jordan Rivera",
        company="Hill Country Communities",
        city="Austin",
    )
    assert not facts.phones
    assert not facts.emails


def test_parse_ddg_html_captures_snippets_and_unwraps():
    hits = parse_ddg_html(DDG_HTML)
    urls = [h.url for h in hits]
    assert any("linkedin.com/in/wayne-broedel" in u for u in urls)
    assert "https://hillcountrycommunities.example/team" in urls
    linkedin = next(h for h in hits if "linkedin.com/in/" in h.url)
    assert "Winter Garden" in linkedin.snippet
    assert linkedin.title


def test_city_from_linkedin_location_snippet():
    hit = SearchHit(
        url="https://www.linkedin.com/in/wayne-broedel-b3196229",
        title="Wayne Broedel - Division President | LinkedIn",
        snippet="Division President · Experience: Maronda Homes · Location: Winter Garden · 500+",
    )
    assert city_from_hit(hit) == "Winter Garden"


def test_classify_and_parse_florida_license():
    url = "https://www.myfloridalicense.com/LicenseDetail.asp?SID=&id=1"
    assert classify_url(url) == "license"
    parsed = parse_florida_license(LICENSE_HTML, url, "Wayne Broedel")
    assert parsed.get("city") == "Apopka"
    assert "CBC1234567" in (parsed.get("note") or "")
    assert parsed["source_url"] == url


def test_parse_bio_page_keeps_source():
    url = "https://orangecsf.org/about/"
    parsed = parse_bio_page(BIO_HTML, url, "Wayne Broedel", "Maronda Homes")
    assert parsed.get("note")
    assert "10,000" in parsed["note"] or "homes" in parsed["note"].lower()
    assert parsed["source_url"] == url


def test_enrich_one_builds_deposit_with_evidence_urls(session, monkeypatch):
    prospect = _seed_prospect(session)
    assert prospect.phone is None

    def fake_search_many(queries, client=None, max_urls=15, **kwargs):
        return [
            SearchHit(
                url="https://hillcountrycommunities.example/team",
                title="Team",
                snippet="Jordan Rivera in Austin, TX",
            )
        ]

    def fake_fetch(url, client=None):
        return FetchedPage(
            url=url,
            final_url=url,
            text=CONTACT_HTML,
            content_type="text/html",
            ok=True,
        )

    monkeypatch.setattr("crm.enrich.engine.search_many", fake_search_many)
    monkeypatch.setattr("crm.enrich.engine.fetch_url", fake_fetch)
    monkeypatch.setattr(
        "crm.enrich.engine.company_seed_urls",
        lambda website, domain: ["https://hillcountrycommunities.example/team"],
    )

    client = MagicMock(spec=httpx.Client)
    item = enrich_one(prospect, client=client)
    assert item.record is not None
    assert item.record["prospect_id"] == prospect.id
    assert item.record["phone"] == "(512) 555-0134"
    assert item.record["evidence"]
    assert all(e.get("url") for e in item.record["evidence"])


def test_enrich_one_uses_serp_linkedin_and_license_without_fabricating(session, monkeypatch):
    prospect = _seed_prospect(
        session,
        full_name="Wayne Broedel",
        company={
            "name": "Maronda Homes",
            "domain": "marondahomes.com",
            "website": "https://www.marondahomes.com",
            "region": "Florida",
        },
        evidence=[{"url": "https://www.marondahomes.com/about", "note": "company"}],
    )

    def fake_search_many(queries, client=None, max_urls=15, **kwargs):
        return [
            SearchHit(
                url="https://www.linkedin.com/in/wayne-broedel-b3196229/",
                title="Wayne Broedel - Division President - Maronda Homes | LinkedIn",
                snippet="Winter Garden, Florida · Experience: Maronda Homes",
            ),
            SearchHit(
                url="https://www.myfloridalicense.com/LicenseDetail.asp?id=9",
                title="Licensee Detail",
                snippet="WAYNE BROEDEL Apopka",
            ),
            SearchHit(
                url="https://orangecsf.org/about/",
                title="About — Orange CSF",
                snippet="Wayne Broedel Division President",
            ),
        ]

    pages = {
        "https://www.myfloridalicense.com/LicenseDetail.asp?id=9": LICENSE_HTML,
        "https://orangecsf.org/about/": BIO_HTML,
        "https://www.linkedin.com/in/wayne-broedel-b3196229/": (
            "<html><body>Wayne Broedel Maronda Homes Winter Garden, FL</body></html>"
        ),
    }

    def fake_fetch(url, client=None):
        return FetchedPage(
            url=url,
            final_url=url,
            text=pages.get(url, EMPTY_HTML),
            content_type="text/html",
            ok=True,
        )

    monkeypatch.setattr("crm.enrich.engine.search_many", fake_search_many)
    monkeypatch.setattr("crm.enrich.engine.fetch_url", fake_fetch)
    monkeypatch.setattr("crm.enrich.engine.company_seed_urls", lambda *a, **k: [])

    item = enrich_one(prospect, client=MagicMock(spec=httpx.Client))
    assert item.record is not None
    assert "linkedin.com/in/wayne-broedel" in item.record.get("linkedin_url", "")
    assert item.record.get("city") in {"Winter Garden", "Apopka"}
    assert item.record.get("notes")
    urls = {e["url"] for e in item.record["evidence"]}
    assert any("linkedin.com/in/" in u for u in urls)
    assert any("myfloridalicense.com" in u for u in urls)
    assert any("orangecsf.org" in u for u in urls)


def test_enrich_one_no_fabricated_facts_on_empty_pages(session, monkeypatch):
    prospect = _seed_prospect(session)

    monkeypatch.setattr(
        "crm.enrich.engine.search_many",
        lambda *a, **k: [SearchHit(url="https://empty.example/")],
    )
    monkeypatch.setattr(
        "crm.enrich.engine.fetch_url",
        lambda url, client=None: FetchedPage(
            url=url, final_url=url, text=EMPTY_HTML, content_type="text/html", ok=True
        ),
    )
    monkeypatch.setattr("crm.enrich.engine.company_seed_urls", lambda *a, **k: [])

    item = enrich_one(prospect, client=MagicMock(spec=httpx.Client))
    assert item.record is None
    assert item.shortfalls


def test_write_enrich_deposit_and_ingest(session, tmp_path, monkeypatch):
    prospect = _seed_prospect(session)
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    monkeypatch.setattr("crm.config.INBOX_DIR", inbox)

    result = EnrichResult(request_id=None, items=[])
    from crm.enrich.engine import ProspectEnrichment

    result.items.append(
        ProspectEnrichment(
            prospect_id=prospect.id,
            full_name=prospect.full_name,
            company_name=prospect.company.name,
            record={
                "prospect_id": prospect.id,
                "full_name": prospect.full_name,
                "company": {"name": prospect.company.name, "domain": prospect.company.domain},
                "phone": "(512) 555-0134",
                "city": "Austin",
                "evidence": [
                    {"url": "https://hillcountrycommunities.example/team", "note": "found phone"}
                ],
            },
        )
    )
    path = write_enrich_deposit(result, inbox_dir=inbox)
    assert path and path.exists()
    data = path.read_text(encoding="utf-8")
    assert '"schema_version": 3' in data
    assert '"source": "enricher"' in data
    assert f'"prospect_id": {prospect.id}' in data

    from crm.ingest import ingest_deposit_json

    summary = ingest_deposit_json(session, data, filename=path.name)
    assert summary.enriched == 1
    session.flush()
    session.refresh(prospect)
    assert prospect.phone == "(512) 555-0134"
    assert prospect.city == "Austin"


def test_enrich_prospects_skips_closed(session, monkeypatch):
    open_p = _seed_prospect(session)
    ingest_records(
        session,
        [{
            "company": {"name": "Nope Co", "domain": "nope.example"},
            "full_name": "Nope Person",
            "title": "VP",
            "icp_score": 50,
            "icp_rationale": "x",
            "evidence": [{"url": "https://nope.example/t", "note": "t"}],
            "status": "not_fit",
        }],
        filename="closed.json",
        source="codex",
    )
    closed = session.scalars(select(Prospect).where(Prospect.full_name == "Nope Person")).one()

    monkeypatch.setattr("crm.enrich.engine.search_many", lambda *a, **k: [])
    monkeypatch.setattr(
        "crm.enrich.engine.fetch_url",
        lambda url, client=None: FetchedPage(
            url=url, final_url=url, text=EMPTY_HTML, content_type="text/html", ok=True
        ),
    )
    monkeypatch.setattr("crm.enrich.engine.company_seed_urls", lambda *a, **k: [])

    result = enrich_prospects(session, [open_p.id, closed.id], request_id=9)
    assert len(result.items) == 1
    assert result.items[0].prospect_id == open_p.id
