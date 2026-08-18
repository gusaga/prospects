"""Normalization and duplicate detection.

Exact duplicate  = same company + same normalized full name  -> skip (or enrich
empty fields). Near duplicate = looks suspiciously similar     -> parked in the
review queue for a human decision, never silently merged.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Prospect

# Nicknames that commonly appear interchangeably on team pages.
_NICKNAMES = {
    "mike": "michael", "bill": "william", "will": "william", "bob": "robert",
    "rob": "robert", "bobby": "robert", "jim": "james", "jimmy": "james",
    "tom": "thomas", "tim": "timothy", "dave": "david", "dan": "daniel",
    "danny": "daniel", "chris": "christopher", "steve": "steven", "ed": "edward",
    "eddie": "edward", "ted": "edward", "tony": "anthony", "rick": "richard",
    "dick": "richard", "rich": "richard", "greg": "gregory", "jeff": "jeffrey",
    "joe": "joseph", "josh": "joshua", "matt": "matthew", "nick": "nicholas",
    "pat": "patrick", "sam": "samuel", "andy": "andrew", "drew": "andrew",
    "ken": "kenneth", "kenny": "kenneth", "larry": "lawrence", "ron": "ronald",
    "gus": "gustavo", "beth": "elizabeth", "liz": "elizabeth", "betty": "elizabeth",
    "kate": "katherine", "katie": "katherine", "kathy": "katherine",
    "sue": "susan", "suzie": "susan", "peggy": "margaret", "maggie": "margaret",
}


def normalize_text(value: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", " ", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


def name_key(full_name: str) -> str:
    """Dedupe key for a person: normalized, minus middle initials and suffixes."""
    # Apostrophes join, not separate: O'Brien -> obrien.
    parts = normalize_text(full_name.replace("'", "").replace("’", "")).split()
    parts = [p for p in parts if p not in {"jr", "sr", "ii", "iii", "iv"}]
    # Drop single-letter middle initials ("l clint richardson" -> first+last kept)
    if len(parts) > 2:
        parts = [parts[0]] + [p for p in parts[1:-1] if len(p) > 1] + [parts[-1]]
    return " ".join(parts)


def canonical_first(first: str) -> str:
    return _NICKNAMES.get(first, first)


def normalize_domain(value: str | None) -> str | None:
    """'https://www.Acme.com/team' -> 'acme.com'. Returns None if unusable."""
    if not value or not value.strip():
        return None
    raw = value.strip().casefold()
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def find_company(session: Session, *, domain: str | None, name: str) -> Company | None:
    if domain:
        company = session.scalar(select(Company).where(Company.domain == domain))
        if company:
            return company
    normalized = normalize_text(name)
    for candidate in session.scalars(select(Company)):
        if normalize_text(candidate.name) == normalized:
            return candidate
    return None


def find_exact_prospect(session: Session, company_id: int, full_name: str) -> Prospect | None:
    return session.scalar(
        select(Prospect).where(Prospect.company_id == company_id, Prospect.name_key == name_key(full_name))
    )


def find_near_duplicate(
    session: Session,
    *,
    company_id: int | None,
    full_name: str,
    email: str | None,
    phone: str | None,
    linkedin_url: str | None,
) -> tuple[Prospect, str] | None:
    """Return (existing prospect, human-readable reason) for a suspicious match."""
    email_norm = email.strip().casefold() if email else None
    phone_norm = re.sub(r"\D", "", phone) if phone else None
    linkedin_norm = (linkedin_url or "").strip().casefold().rstrip("/") or None

    for existing in session.scalars(select(Prospect)):
        if email_norm and existing.email and existing.email.strip().casefold() == email_norm:
            return existing, f"same email ({existing.email})"
        if phone_norm and existing.phone and re.sub(r"\D", "", existing.phone) == phone_norm:
            return existing, f"same phone ({existing.phone})"
        if linkedin_norm and existing.linkedin_url and existing.linkedin_url.strip().casefold().rstrip("/") == linkedin_norm:
            return existing, "same LinkedIn profile"

    if company_id is None:
        return None

    incoming = name_key(full_name).split()
    if not incoming:
        return None
    in_first, in_last = incoming[0], incoming[-1]
    for existing in session.scalars(select(Prospect).where(Prospect.company_id == company_id)):
        parts = existing.name_key.split()
        if not parts:
            continue
        ex_first, ex_last = parts[0], parts[-1]
        if ex_last != in_last:
            continue
        if canonical_first(ex_first) == canonical_first(in_first) or ex_first[0] == in_first[0]:
            return existing, f"similar name at same company ({existing.full_name})"
    return None
