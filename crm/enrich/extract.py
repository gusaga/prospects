"""Extract phones, emails, LinkedIn, city hints from public HTML — each with a source URL."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4})(?!\d)"
)
EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
LINKEDIN_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_\-%]+/?",
    re.IGNORECASE,
)
# Common US city, ST patterns in prose
CITY_STATE_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?),\s*([A-Z]{2})\b"
)

SKIP_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "googleapis.com",
}


@dataclass
class SourcedValue:
    """One fact the BDR can verify at source_url."""

    value: str
    source_url: str
    note: str = ""


@dataclass
class PageFacts:
    phones: list[SourcedValue] = field(default_factory=list)
    emails: list[SourcedValue] = field(default_factory=list)
    linkedin_urls: list[SourcedValue] = field(default_factory=list)
    cities: list[SourcedValue] = field(default_factory=list)
    photo_urls: list[SourcedValue] = field(default_factory=list)
    notes: list[SourcedValue] = field(default_factory=list)

    def has_facts(self) -> bool:
        return bool(
            self.phones
            or self.emails
            or self.linkedin_urls
            or self.cities
            or self.photo_urls
            or self.notes
        )

    def evidence_rows(self) -> list[dict[str, str]]:
        """One evidence entry per distinct source URL, noting what was found there."""
        by_url: dict[str, list[str]] = {}
        for label, items in (
            ("phone", self.phones),
            ("email", self.emails),
            ("LinkedIn", self.linkedin_urls),
            ("city", self.cities),
            ("photo", self.photo_urls),
            ("note", self.notes),
        ):
            for item in items:
                by_url.setdefault(item.source_url, [])
                if label not in by_url[item.source_url]:
                    by_url[item.source_url].append(label)
                # Prefer the item's own note when present (license/bio)
                if item.note and item.note not in by_url[item.source_url]:
                    # Keep structured labels; append rich note separately below
                    pass
        rows: list[dict[str, str]] = []
        rich_notes = {
            item.source_url: item.note
            for item in self.notes
            if item.note and item.source_url
        }
        for url, labels in by_url.items():
            note = rich_notes.get(url) or ("found " + ", ".join(labels))
            rows.append({"url": url, "note": note[:500]})
        return rows


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return raw.strip()
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return unescape(re.sub(r"\s+", " ", text))


def name_mentioned(text: str, full_name: str) -> bool:
    if not full_name:
        return False
    low = text.lower()
    if full_name.lower() in low:
        return True
    parts = [p for p in full_name.split() if len(p) > 1]
    if len(parts) >= 2:
        return parts[0].lower() in low and parts[-1].lower() in low
    return False


def company_mentioned(text: str, company: str) -> bool:
    if not company:
        return False
    return company.lower() in text.lower()


def _add_unique(bucket: list[SourcedValue], value: str, source_url: str, note: str = "") -> None:
    value = (value or "").strip()
    if not value or not source_url:
        return
    key = value.lower()
    if any(existing.value.lower() == key for existing in bucket):
        return
    bucket.append(SourcedValue(value=value, source_url=source_url, note=note))


def extract_from_html(
    html: str,
    *,
    page_url: str,
    full_name: str,
    company: str,
    city: str | None,
) -> PageFacts:
    """Pull contact facts only when the page looks related to the person/company."""
    facts = PageFacts()
    soup = BeautifulSoup(html, "html.parser")
    text = _visible_text(soup)
    if not text:
        return facts

    related = name_mentioned(text, full_name) or company_mentioned(text, company)
    if not related:
        host = urlparse(page_url).netloc.lower()
        company_token = company.lower().replace(" ", "")
        host_token = host.replace("-", "").replace(".", "")
        if company_token not in host_token:
            last = full_name.split()[-1].lower() if full_name else ""
            for match in LINKEDIN_RE.findall(html):
                if last and last in match.lower():
                    _add_unique(facts.linkedin_urls, match.rstrip("/"), page_url, "LinkedIn on page")
            return facts

    for match in PHONE_RE.findall(text):
        _add_unique(facts.phones, _normalize_phone(match), page_url, "phone on page")

    for match in EMAIL_RE.findall(text):
        email = match.strip()
        domain = email.split("@", 1)[-1].lower()
        if domain in SKIP_EMAIL_DOMAINS:
            continue
        _add_unique(facts.emails, email, page_url, "email on page")

    for match in LINKEDIN_RE.findall(html):
        _add_unique(facts.linkedin_urls, match.rstrip("/"), page_url, "LinkedIn URL on page")

    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if "linkedin.com/in/" in href.lower():
            clean = href.split("?")[0].rstrip("/")
            _add_unique(facts.linkedin_urls, clean, page_url, "LinkedIn link on page")

    if not city:
        for match in CITY_STATE_RE.finditer(text[:4000]):
            city_name = f"{match.group(1)}, {match.group(2)}"
            _add_unique(facts.cities, city_name, page_url, "city/state on page")
            if len(facts.cities) >= 3:
                break

    if name_mentioned(text, full_name):
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if full_name.split()[-1].lower() not in sentence.lower():
                continue
            if any(
                w in sentence.lower()
                for w in ("project", "community", "development", "announced", "spoke", "permit")
            ):
                note = sentence.strip()
                if 40 <= len(note) <= 280:
                    _add_unique(facts.notes, note, page_url, "bio/news snippet")
                    break

    if related or name_mentioned(text, full_name):
        from ..photos import guess_image_urls_from_html

        for url in guess_image_urls_from_html(html, page_url, full_name):
            _add_unique(facts.photo_urls, url, page_url, "photo candidate on page")

    return facts
