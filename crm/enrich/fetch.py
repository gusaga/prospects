"""Polite HTTP fetch for public HTML pages."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = (
    "ProspectingCRM-Enricher/1.0 (+local; research; mailto:local-only)"
)
TIMEOUT = httpx.Timeout(12.0, connect=8.0)
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 5


@dataclass
class FetchedPage:
    url: str
    final_url: str
    text: str
    content_type: str
    ok: bool
    error: str | None = None


def _looks_html(content_type: str, url: str) -> bool:
    ct = (content_type or "").lower()
    if "html" in ct or "text/plain" in ct or ct == "":
        return True
    path = urlparse(url).path.lower()
    return path.endswith((".html", ".htm", "/")) or path == ""


def fetch_url(
    url: str,
    *,
    client: httpx.Client | None = None,
) -> FetchedPage:
    """GET a URL; return HTML text or an error. Never raises for HTTP failures."""
    owns = client is None
    client = client or httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=TIMEOUT,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
    )
    try:
        response = client.get(url)
        ctype = response.headers.get("content-type", "")
        if not _looks_html(ctype, str(response.url)):
            return FetchedPage(
                url=url,
                final_url=str(response.url),
                text="",
                content_type=ctype,
                ok=False,
                error=f"non-HTML content-type: {ctype}",
            )
        raw = response.content[:MAX_BYTES]
        try:
            text = raw.decode(response.encoding or "utf-8", errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        if response.status_code >= 400:
            return FetchedPage(
                url=url,
                final_url=str(response.url),
                text=text,
                content_type=ctype,
                ok=False,
                error=f"HTTP {response.status_code}",
            )
        return FetchedPage(
            url=url,
            final_url=str(response.url),
            text=text,
            content_type=ctype,
            ok=True,
        )
    except httpx.HTTPError as exc:
        return FetchedPage(
            url=url,
            final_url=url,
            text="",
            content_type="",
            ok=False,
            error=str(exc),
        )
    finally:
        if owns:
            client.close()


def company_seed_urls(website: str | None, domain: str | None) -> list[str]:
    """Likely public contact pages on the company site."""
    bases: list[str] = []
    if website and website.startswith(("http://", "https://")):
        bases.append(website.rstrip("/") + "/")
    elif domain:
        bases.append(f"https://{domain.lstrip('/')}/")
    paths = ("", "contact", "contact-us", "about", "about-us", "team", "leadership", "our-team")
    urls: list[str] = []
    for base in bases:
        for path in paths:
            candidate = urljoin(base, path) if path else base.rstrip("/")
            if candidate and candidate not in urls:
                urls.append(candidate)
    return urls[:10]
