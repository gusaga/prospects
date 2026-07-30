"""Constrained task prompts for public, evidence-backed browser research."""

from __future__ import annotations

import json

from .schemas import ICPProfile, TargetAccountType


PUBLIC_RESEARCH_GUARDRAILS = """
You are conducting public, professional web research for an internal prospecting tool.
Only use pages that are publicly accessible without a login. Do not submit forms, bypass
paywalls/CAPTCHAs, use a browser profile, infer email addresses, or collect sensitive or
personal-life information. Do not use data broker or B2B enrichment APIs. Do not invent facts.
For every returned field, provide a public source URL and a concise supporting excerpt. If a
claim cannot be supported, omit it. Prefer official company pages and independent public sources.
""".strip()


def _icp_context(icp: ICPProfile) -> str:
    return json.dumps(icp.model_dump(mode="json"), indent=2)


def _role_guidance(icp: ICPProfile) -> str:
    target_titles = "\n".join(f"- {title}" for title in icp.target_job_titles)
    adjacent_titles = "\n".join(f"- {title}" for title in icp.adjacent_personas)
    return f"""
Return people only when their published title is one of these target or adjacent personas,
or a clearly equivalent written form:
Target titles:
{target_titles}
Adjacent personas:
{adjacent_titles}

Treat VP and Vice President as equivalent, and ignore harmless punctuation and connector words
such as commas and "of". Do not treat a different executive title as a match merely because it
is senior. A title must still be published verbatim on a public source.
""".strip()


def account_discovery_prompt(icp: ICPProfile, limit: int) -> str:
    account_audience = ""
    if icp.target_account_type == TargetAccountType.OWNER_DEVELOPER:
        account_audience = """
This is a direct-owner/developer run. Return only actual property owners, land developers,
master developers, or homebuilders, with official-site evidence that the company owns,
acquires, entitles, develops, or builds its communities. Exclude engineering, surveying,
planning, architecture, consulting, and construction-management firms even when a person's
title includes Land Development. Set each returned company's account_type to
owner_developer and include the supporting official evidence.
""".strip()
    return f"""
{PUBLIC_RESEARCH_GUARDRAILS}

Find {limit} distinct companies that match this ideal customer profile. This is a delivery target,
not a suggestion to stop after a few examples:
{_icp_context(icp)}

Use ordinary public web search and official company websites. Work through each requested
geography and relevant industry/notes phrase with varied queries. Return only companies with a
canonical website/domain and official-site evidence that relates them to the ICP. A directory may
surface a candidate but cannot be the primary evidence. Skip duplicate domains and include no
contacts in this task. If public sources are genuinely exhausted before {limit}, return the best
verified set and state the concrete search limitation in the result notes.

{account_audience}
""".strip()


def contact_discovery_prompt(icp: ICPProfile, company_name: str, company_url: str, limit: int) -> str:
    return f"""
{PUBLIC_RESEARCH_GUARDRAILS}

Research {company_name} ({company_url}) for at most {limit} people matching this ICP:
{_icp_context(icp)}

Start on the company domain and use public About, Team, Leadership, Press, and Careers pages.
{_role_guidance(icp)}

{"Confirm the company is the owner/developer itself, not a professional-services provider." if icp.target_account_type == TargetAccountType.OWNER_DEVELOPER else ""}

Return an explicitly published name, job role, optional published work contact details, and
evidence. For every verified person, make a focused pass for a publicly displayed direct work
email, business phone, or public professional profile on official company pages, team bios,
press releases, or other public no-login sources. Copy a contact method only when it appears
verbatim on the cited page; never infer an email pattern, substitute a generic company mailbox,
or guess a phone number. Identify buying-committee role only when the public job role supports
it. Also return other clearly evidenced committee members when appropriate. Do not use unrelated
titles to fill the limit.
""".strip()


def rapport_research_prompt(
    icp: ICPProfile,
    company_name: str,
    company_url: str,
    prospect_name: str,
    prospect_role: str,
) -> str:
    return f"""
{PUBLIC_RESEARCH_GUARDRAILS}

For {prospect_name}, {prospect_role} at {company_name} ({company_url}), find at most three
professional rapport signals and at most five account signals relevant to this ICP:
{_icp_context(icp)}

Rapport signals may come from public company news, conference bios, nonprofit boards, university
directories, or press releases. Use only these relationship-context categories: professional,
education, projects, or research. Keep findings professional and relevant; exclude family,
private-location, health, politics, or other sensitive information. Account signals should cover
hiring, leadership, expansion, product, or news developments. Cite every finding.
""".strip()
