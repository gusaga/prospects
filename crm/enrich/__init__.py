"""Local Stage-2 enricher: public web search + page scrape, no paid APIs.

Follows the BDR research playbook (name+company → LinkedIn → license/bio →
contact hunt). Every deposited fact carries a reference URL in `evidence`
so a human can verify it. Writes deposit JSON into inbox/ for ingest.
"""

from .engine import EnrichResult, enrich_prospects
from .deposit import write_enrich_deposit

__all__ = ["EnrichResult", "enrich_prospects", "write_enrich_deposit"]
