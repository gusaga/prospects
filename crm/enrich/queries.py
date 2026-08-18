"""Search query builders — BDR playbook: name + company first, then specialty sources."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchAnchors:
    full_name: str
    company: str
    city: str | None
    region: str | None
    domain: str | None = None

    @property
    def place(self) -> str:
        return (self.city or self.region or "").strip()

    @property
    def in_florida(self) -> bool:
        blob = " ".join(x for x in (self.city, self.region) if x).lower()
        return "florida" in blob or bool(re.search(r"(?:^|[\s,])fl(?:$|[\s,])", blob))


def build_queries(anchors: SearchAnchors) -> list[str]:
    """Ordered queries matching how a BDR researches a prospect by hand."""
    name = anchors.full_name.strip()
    company = anchors.company.strip()
    place = anchors.place
    queries: list[str] = []

    def add(q: str) -> None:
        q = " ".join(q.split())
        if q and q not in queries:
            queries.append(q)

    # 1) Primary Google-style query (highest value)
    add(f"{name} {company}")
    add(f'"{name}" "{company}"')

    # 2) LinkedIn
    add(f"{name} {company} linkedin")
    add(f'site:linkedin.com/in "{name}" {company}')

    # 3) State license / public professional records (when geography suggests it)
    if anchors.in_florida:
        add(f'{name} {company} myfloridalicense OR "department of business"')
        add(f'"{name}" license Florida contractor OR broker')
    elif place:
        add(f'{name} {company} license OR "department of" {place}')

    # 4) Local bios / news / foundations
    add(f'{name} {company} "vice president" OR director OR about OR bio')
    if place:
        add(f"{name} {company} {place}")

    # 5) Company site
    if anchors.domain:
        add(f'site:{anchors.domain} "{name}"')

    # 6) Contact hunt last (after identity is locked)
    add(f'"{name}" {company} phone OR contact OR email')

    # Keep the list short — too many queries triggers search-engine bot walls.
    return queries[:6]
