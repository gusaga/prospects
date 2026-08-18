"""Public web search (no API keys): ddgs primary, HTML DDG/Bing fallback.

Reads titles + snippets the same way a BDR scans Google results — LinkedIn URL,
city, license hits — before any page scrape.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from .fetch import TIMEOUT
from .sources import source_rank

SEARCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

DDG_HTML = "https://html.duckduckgo.com/html/"
BING_HTML = "https://www.bing.com/search"

BLOCKED_HOST_FRAGMENTS = (
    "linkedin.com/login",
    "facebook.com/login",
    "accounts.google",
    "duckduckgo.com",
)

CITY_STATE_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?),\s*"
    r"(Florida|Texas|Arizona|Georgia|Tennessee|North Carolina|South Carolina|"
    r"Alabama|California|Colorado|Nevada|Utah|Oklahoma|Louisiana|"
    r"FL|TX|AZ|GA|TN|NC|SC|AL|CA|CO|NV|UT|OK|LA)\b"
)
LOCATION_LABEL_RE = re.compile(
    r"(?:Location|Lives in|Based in)\s*[:·\-]\s*"
    r"([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)",
    re.IGNORECASE,
)
# "based in Winter Garden, FL" (lowercase based)
BASED_IN_RE = re.compile(
    r"\bbased in\s+([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)\s*,\s*([A-Z]{2})\b",
    re.IGNORECASE,
)
LINKEDIN_IN_TEXT_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_\-%]+/?",
    re.IGNORECASE,
)
ANOMALY_MARKERS = (
    "anomaly-modal",
    "Unfortunately, bots use DuckDuckGo too",
    "challenge-form",
    "captcha",
)


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    query: str = ""

    @property
    def rank(self) -> int:
        return source_rank(self.url)


@dataclass
class SearchDiagnostics:
    queries_run: int = 0
    hits: int = 0
    backend: str = ""
    blocked: bool = False
    sample_urls: list[str] = field(default_factory=list)
    error: str | None = None


def _unwrap_ddg_redirect(href: str) -> str | None:
    href = unescape(href.strip())
    if href.startswith("//"):
        href = "https:" + href
    if not href.startswith("http"):
        return None
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        target = qs.get("uddg", [None])[0]
        if target:
            return unquote(target)
    return href


def _clean_url(url: str | None, *, bing: bool = False) -> str | None:
    if not url:
        return None
    url = _unwrap_ddg_redirect(url)
    if not url or not url.startswith("http"):
        return None
    low = url.lower()
    if any(b in low for b in BLOCKED_HOST_FRAGMENTS):
        return None
    host = urlparse(url).netloc.lower()
    if "duckduckgo.com" in host:
        return None
    if bing and host.endswith("bing.com"):
        return None
    if "linkedin.com/in/" in low:
        url = url.split("?")[0].rstrip("/")
    return url.split("#")[0]


def parse_ddg_html(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html, "html.parser")
    hits: list[SearchHit] = []
    seen: set[str] = set()

    results = soup.select("div.result, div.web-result, div.results_links")
    if not results:
        for match in re.finditer(
            r'href="(https?://[^"]+|/l/\?[^"]+)"',
            html,
            flags=re.IGNORECASE,
        ):
            url = _clean_url(unescape(match.group(1)))
            if url and url not in seen:
                seen.add(url)
                hits.append(SearchHit(url=url))
            if len(hits) >= 15:
                break
        return hits

    for block in results:
        link = block.select_one("a.result__a, a.result-link, a[href]")
        if not link:
            continue
        href = link.get("href") or ""
        if href.startswith("/l/"):
            href = "https://duckduckgo.com" + href
        url = _clean_url(href)
        if not url or url in seen:
            continue
        title = link.get_text(" ", strip=True)
        sn_el = block.select_one(
            "a.result__snippet, div.result__snippet, td.result-snippet, .result__snippet"
        )
        snippet = sn_el.get_text(" ", strip=True) if sn_el else ""
        seen.add(url)
        hits.append(SearchHit(url=url, title=title, snippet=snippet))
        if len(hits) >= 15:
            break
    return hits


def parse_bing_html(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html, "html.parser")
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a[href]")
        if not link:
            continue
        url = _clean_url(link.get("href"), bing=True)
        if not url or url in seen:
            continue
        title = link.get_text(" ", strip=True)
        sn_el = block.select_one(".b_caption p, .b_algoSlug, p")
        snippet = sn_el.get_text(" ", strip=True) if sn_el else ""
        seen.add(url)
        hits.append(SearchHit(url=url, title=title, snippet=snippet))
        if len(hits) >= 15:
            break
    return hits


def city_from_hit(hit: SearchHit) -> str | None:
    """Pull city from SERP title/snippet — City, ST or LinkedIn Location: City.

    LinkedIn results are strict: only ``Location: City`` (snippets often contain
    unrelated job cities like Greenville from suggested posts).
    """
    blob = f"{hit.title} {hit.snippet}"
    if "linkedin.com/in/" in hit.url.lower():
        loc = LOCATION_LABEL_RE.search(blob)
        if loc:
            city = loc.group(1).strip()
            if len(city) >= 3 and city.lower() not in {
                "united", "united states", "remote", "greater", "area", "metro",
            }:
                return city
        return None

    based = BASED_IN_RE.search(blob)
    if based:
        return based.group(1).strip()
    loc = LOCATION_LABEL_RE.search(blob)
    if loc:
        city = loc.group(1).strip()
        if len(city) >= 3 and city.lower() not in {
            "united", "united states", "remote", "greater", "area", "metro",
        }:
            return city
    match = CITY_STATE_RE.search(blob)
    if match:
        city = match.group(1).strip()
        if city.lower() not in {"united states", "north america"}:
            return city
    return None


def linkedin_from_hit(hit: SearchHit) -> str | None:
    if "linkedin.com/in/" in hit.url.lower():
        return hit.url.split("?")[0].rstrip("/")
    for match in LINKEDIN_IN_TEXT_RE.findall(f"{hit.title} {hit.snippet} {hit.url}"):
        return match.rstrip("/")
    return None


def _search_headers() -> dict[str, str]:
    return {
        "User-Agent": SEARCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def search_ddgs(query: str, *, max_results: int = 10) -> list[SearchHit]:
    """Primary backend: ddgs (no API key; works when HTML DDG is bot-walled)."""
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    hits: list[SearchHit] = []
    seen: set[str] = set()
    try:
        with DDGS() as ddgs:
            rows = list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []
    for row in rows:
        url = _clean_url(row.get("href") or row.get("link") or row.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        hits.append(
            SearchHit(
                url=url,
                title=(row.get("title") or "").strip(),
                snippet=(row.get("body") or row.get("snippet") or "").strip(),
                query=query,
            )
        )
    return hits


def search_ddg_html(query: str, *, client: httpx.Client) -> tuple[list[SearchHit], bool]:
    try:
        response = client.post(
            DDG_HTML,
            data={"q": query, "b": ""},
            headers=_search_headers(),
        )
        if response.status_code >= 400:
            return [], True
        html = response.text
        if any(m.lower() in html.lower() for m in ANOMALY_MARKERS):
            return [], True
        hits = parse_ddg_html(html)
        for hit in hits:
            hit.query = query
        return hits, False
    except httpx.HTTPError:
        return [], True


def search_bing(query: str, *, client: httpx.Client) -> list[SearchHit]:
    try:
        response = client.get(
            BING_HTML,
            params={"q": query, "setlang": "en-US"},
            headers=_search_headers(),
        )
        if response.status_code >= 400:
            return []
        hits = parse_bing_html(response.text)
        for hit in hits:
            hit.query = query
        return hits
    except httpx.HTTPError:
        return []


def search_web(
    query: str,
    *,
    client: httpx.Client | None = None,
) -> list[SearchHit]:
    hits = search_ddgs(query)
    if hits:
        return hits
    owns = client is None
    client = client or httpx.Client(
        headers=_search_headers(),
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    try:
        html_hits, blocked = search_ddg_html(query, client=client)
        if html_hits:
            return html_hits
        if blocked or not html_hits:
            return search_bing(query, client=client)
        return []
    finally:
        if owns:
            client.close()


def _serp_ready(hits: list[SearchHit], *, last_name: str) -> bool:
    has_li = False
    has_city = False
    for hit in hits:
        li = linkedin_from_hit(hit)
        if li and (not last_name or last_name in li.lower() or last_name in hit.title.lower()):
            has_li = True
        if city_from_hit(hit):
            has_city = True
    return has_li and has_city


def search_many(
    queries: list[str],
    *,
    client: httpx.Client | None = None,
    max_urls: int = 18,
    full_name: str | None = None,
    diagnostics: SearchDiagnostics | None = None,
) -> list[SearchHit]:
    """Run queries via ddgs first; HTML backends only if needed. Stop early when ready."""
    owns = client is None
    client = client or httpx.Client(
        headers=_search_headers(),
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    found: dict[str, SearchHit] = {}
    backend_used: list[str] = []
    blocked_any = False
    last_name = (full_name or "").split()[-1].lower() if full_name else ""

    try:
        for i, query in enumerate(queries):
            hits = search_ddgs(query, max_results=10)
            if hits:
                if "ddgs" not in backend_used:
                    backend_used.append("ddgs")
            else:
                html_hits, blocked = search_ddg_html(query, client=client)
                if blocked:
                    blocked_any = True
                hits = html_hits
                if hits and "ddg-html" not in backend_used:
                    backend_used.append("ddg-html")
                if not hits:
                    hits = search_bing(query, client=client)
                    if hits and "bing" not in backend_used:
                        backend_used.append("bing")

            if diagnostics is not None:
                diagnostics.queries_run += 1

            for hit in hits:
                prior = found.get(hit.url)
                if prior is None or (len(hit.snippet) > len(prior.snippet)):
                    found[hit.url] = hit

            if _serp_ready(list(found.values()), last_name=last_name):
                break
            if any("linkedin.com/in/" in u for u in found) and i >= 2:
                break
            if i < len(queries) - 1:
                time.sleep(0.35)
    finally:
        if owns:
            client.close()

    hits = list(found.values())
    hits.sort(key=lambda h: (h.rank, h.url))
    hits = hits[:max_urls]
    if diagnostics is not None:
        diagnostics.hits = len(hits)
        diagnostics.backend = "+".join(backend_used) or "none"
        diagnostics.blocked = blocked_any and not hits
        diagnostics.sample_urls = [h.url for h in hits[:6]]
        if not hits:
            diagnostics.error = (
                "search returned 0 results (may be blocked — wait a minute and retry)"
            )
    return hits


def parse_ddg_urls(html: str) -> list[str]:
    return [h.url for h in parse_ddg_html(html)]
