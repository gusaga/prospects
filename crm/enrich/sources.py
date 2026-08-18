"""Classify enrichment URLs and parse high-value public source pages."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

# Lower rank number = fetch/prefer first (BDR playbook order).
SOURCE_RANK = {
    "linkedin": 1,
    "license": 2,
    "bio": 3,
    "people_intel": 3,  # SERP only — city/title in snippets
    "company": 4,
    "news": 5,
    "directory": 8,  # people-finder — last-resort city
    "other": 9,
}

LICENSE_HOSTS = (
    "myfloridalicense.com",
    "www.myfloridalicense.com",
    "www2.myfloridalicense.com",
    "search-floridalicense.com",
)
BIO_HINTS = ("/about", "about/", "/team", "/leadership", "/board", "foundation", "bio")
# Scrape these for city confirm only (never as primary when LinkedIn/intel exists).
DIRECTORY_HOSTS = (
    "floridaresidentsdirectory.com",
    "fastpeoplesearch.com",
    "truepeoplesearch.com",
    "spokeo.com",
    "whitepages.com",
    "beenverified.com",
    "intelius.com",
)
# SERP snippets are gold (city/title); pages are paywalled — never fetch.
PEOPLE_INTEL_HOSTS = (
    "zoominfo.com",
    "rocketreach.co",
    "datanyze.com",
    "apollo.io",
    "lusha.com",
)


_STREET_TOKENS = frozenset({
    "st", "street", "ave", "avenue", "rd", "road", "dr", "drive",
    "ln", "lane", "blvd", "hwy", "way", "ct", "court", "pl", "place",
    "circle", "cir", "main", "n", "s", "e", "w", "ne", "nw", "se", "sw",
})


def _city_before_florida(address_blob: str) -> str | None:
    """From '... Apopka, Florida 32712' or 'Winter Garden, Florida 34787'."""
    match = re.search(r"(.+),\s*Florida\s+\d{5}", address_blob, flags=re.IGNORECASE)
    if not match:
        return None
    tokens = [t for t in match.group(1).replace(",", " ").split() if t]
    if not tokens:
        return None
    city_parts = [tokens[-1]]
    if len(tokens) >= 2:
        prev = tokens[-2].lower().rstrip(".")
        if prev not in _STREET_TOKENS and not any(ch.isdigit() for ch in tokens[-2]):
            if tokens[-2][0].isalpha():
                city_parts.insert(0, tokens[-2])
    return " ".join(city_parts).title()


def classify_url(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    path = urlparse(url).path.lower()
    if "linkedin.com" in host and "/in/" in path:
        return "linkedin"
    if any(h in host for h in LICENSE_HOSTS) or "myfloridalicense" in host:
        return "license"
    if any(h in host for h in DIRECTORY_HOSTS):
        return "directory"
    if any(h in host for h in PEOPLE_INTEL_HOSTS):
        return "people_intel"
    if any(h in path or h in host for h in BIO_HINTS):
        return "bio"
    if any(x in path for x in ("/news", "/press", "/article", "/blog")):
        return "news"
    return "other"


def source_rank(url: str) -> int:
    return SOURCE_RANK.get(classify_url(url), 9)


def parse_florida_license(html: str, page_url: str, full_name: str) -> dict:
    """Extract public license facts (city/address cues + call-ammo note)."""
    from .extract import CITY_STATE_RE, name_mentioned

    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    if not name_mentioned(text, full_name):
        return {}

    out: dict = {"source_url": page_url, "kind": "license"}
    # Main Address: … Apopka, Florida 32712
    addr = re.search(
        r"Main Address:\s*(.+?Florida\s+\d{5})",
        text,
        flags=re.IGNORECASE,
    )
    if addr:
        out["address"] = addr.group(1).strip()
        city = _city_before_florida(addr.group(1))
        if city:
            out["city"] = city

    if "city" not in out:
        for match in CITY_STATE_RE.finditer(text):
            if match.group(2).upper() == "FL":
                out["city"] = match.group(1)
                break

    lic = re.search(r"License Number:\s*([A-Z0-9]+)", text, flags=re.IGNORECASE)
    if lic:
        out["license_number"] = lic.group(1)
    lic_type = re.search(r"License Type:\s*([A-Za-z /]+)", text, flags=re.IGNORECASE)
    if lic_type:
        out["license_type"] = lic_type.group(1).strip()
    dba = re.search(
        r"(?:DBA Name|Doing Business As)\s*[:;]\s*([A-Z0-9 ,.&'-]{3,80})",
        text,
        flags=re.IGNORECASE,
    )
    if dba:
        dba_name = dba.group(1).strip(" .;")
        # Skip portal chrome like "Customer Contact Center"
        junk = ("customer contact", "contact center", "click here", "verify")
        if not any(j in dba_name.lower() for j in junk):
            out["dba"] = dba_name.title()

    bits = []
    if out.get("license_type") and out.get("license_number"):
        bits.append(f"{out['license_type']} ({out['license_number']})")
    elif out.get("license_number"):
        bits.append(f"FL license {out['license_number']}")
    if out.get("dba"):
        bits.append(f"DBA {out['dba']}")
    if out.get("address"):
        bits.append(f"address on file: {out['address']}")
    if bits:
        out["note"] = "FL DBPR/myfloridalicense: " + "; ".join(bits)
    return out


def parse_bio_page(html: str, page_url: str, full_name: str, company: str) -> dict:
    """Pull rapport paragraphs from about/team/foundation bios."""
    from .extract import name_mentioned

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    if not name_mentioned(text, full_name):
        return {}

    # Prefer the chunk around the person's name
    low = text.lower()
    key = full_name.lower()
    idx = low.find(key)
    window = text[idx : idx + 900] if idx >= 0 else text[:900]
    sentences = re.split(r"(?<=[.!?])\s+", window)
    keep = []
    last = full_name.split()[-1].lower()
    for sentence in sentences:
        s = sentence.strip()
        if len(s) < 40 or len(s) > 320:
            continue
        sl = s.lower()
        if last not in sl and company.lower().split()[0] not in sl:
            continue
        if any(
            w in sl
            for w in (
                "president", "vice", "land", "home", "develop", "year",
                "university", "florida", "community", "division", "partner",
            )
        ):
            keep.append(s)
        if len(keep) >= 3:
            break
    if not keep:
        return {}
    note = " ".join(keep)
    if len(note) > 900:
        note = note[:897] + "…"
    return {"source_url": page_url, "kind": "bio", "note": note}


def city_from_directory_snippet(snippet: str) -> str | None:
    """People-finder pages: only harvest city/state, never DOB/relatives/party."""
    from .extract import CITY_STATE_RE

    match = CITY_STATE_RE.search(snippet or "")
    if match:
        return match.group(1)
    # "Apopka, 32712 Florida" style
    m2 = re.search(
        r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?),\s*\d{5}\s*Florida\b",
        snippet or "",
    )
    if m2:
        return m2.group(1)
    return None
