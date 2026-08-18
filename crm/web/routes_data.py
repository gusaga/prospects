"""Routes for getting data in and out: import page, duplicate review,
ICP settings, board, stats, and the research-brief generator."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .. import config
from ..backup import backup_now, latest_backup
from ..inbox import pending_files, sweep_inbox
from ..ingest import ingest_csv, resolve_dupe
from ..models import (
    CLOSED_STATUSES,
    STATUSES,
    Activity,
    Company,
    DupeReview,
    ImportBatch,
    Prospect,
    ResearchRequest,
    Setting,
    utc_now,
)

router = APIRouter()

# Empty starter — any industry works once the user fills Settings.
DEFAULT_ICP = {
    "product": "",
    "industry": "",
    "company_size": "",
    "regions": [],
    "region_note": "",
    "target_titles": [],
    "adjacent_titles": [],
    "account_rule": "",
    "pain_points": [],
    "notes": "",
}

# One worked example (land / homebuilding). Loadable from Settings — not forced.
EXAMPLE_ICP = {
    "product": "Project-management SaaS for land development teams",
    "industry": "Single-family residential land development / homebuilding",
    "company_size": "11-50",
    "regions": ["Texas", "Arizona", "Florida", "Georgia", "North Carolina", "Tennessee"],
    "region_note": "Sun Belt broadly — TX/AZ/FL first, neighbors welcome",
    "target_titles": ["VP of Land Development"],
    "adjacent_titles": [
        "Senior Land Development Manager",
        "Division President",
        "VP of Acquisitions",
    ],
    "account_rule": (
        "Owner/developers, master developers, and homebuilders only. Exclude engineering, "
        "surveying, planning, architecture, consulting, and construction-management firms "
        "even if they employ matching titles."
    ),
    "pain_points": ["Multiple tools", "lack of centralized database"],
    "notes": "Single-family residential, home builder and land development management.",
}


def get_session(request: Request):
    session = request.app.state.factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def render(request: Request, name: str, **context) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, context)


def get_icp(session: Session) -> dict:
    row = session.get(Setting, "icp")
    icp = dict(DEFAULT_ICP)
    if row and isinstance(row.value, dict):
        icp.update(row.value)
    # Normalize list fields so templates never blow up on old rows.
    for key in ("regions", "target_titles", "adjacent_titles", "pain_points"):
        val = icp.get(key)
        if isinstance(val, str):
            icp[key] = _lines(val)
        elif not isinstance(val, list):
            icp[key] = []
    return icp


def icp_is_ready(icp: dict) -> bool:
    """Minimum fields so briefs and enrichment know who to hunt."""
    return bool(
        (icp.get("product") or "").strip()
        and (icp.get("industry") or "").strip()
        and (icp.get("target_titles") or [])
        and (icp.get("regions") or [])
        and (icp.get("account_rule") or "").strip()
    )


def icp_missing(icp: dict) -> list[str]:
    checks = [
        ("product", "What you sell"),
        ("industry", "Target industry"),
        ("regions", "Regions"),
        ("target_titles", "Target titles"),
        ("account_rule", "Who counts as an account (and who to exclude)"),
    ]
    missing = []
    for key, label in checks:
        val = icp.get(key)
        ok = bool(val and (val if not isinstance(val, str) else val.strip()))
        if not ok:
            missing.append(label)
    return missing


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.replace(",", "\n").splitlines() if line.strip()]


# ---- research requests -----------------------------------------------------


def delivered_count(session: Session, req: ResearchRequest) -> int:
    from sqlalchemy import func

    column = ImportBatch.enriched_count if req.kind == "enrich" else ImportBatch.created_count
    return session.scalar(
        select(func.coalesce(func.sum(column), 0)).where(ImportBatch.request_id == req.id)
    ) or 0


@router.post("/requests")
def create_request(
    request: Request,
    session: Session = Depends(get_session),
    count: int = Form(10),
    region_focus: str = Form(""),
    title_focus: str = Form(""),
    notes: str = Form(""),
):
    count = max(1, min(100, count))
    req = ResearchRequest(
        requested_count=count,
        region_focus=region_focus.strip() or None,
        title_focus=title_focus.strip() or None,
        notes=notes.strip() or None,
    )
    session.add(req)
    session.flush()
    return RedirectResponse(
        f"/requests/{req.id}/brief", status_code=303
    )


# Keep enrich jobs small — pick a few people, not a whole list.
ENRICH_TARGET_CAP = 10


def _parse_enrich_ids(form) -> list[int]:
    """Accept one prospect_id, repeated prospect_ids, or a comma list."""
    ids: list[int] = []
    if form.get("prospect_id"):
        try:
            ids.append(int(form["prospect_id"]))
        except (TypeError, ValueError):
            pass
    for raw in form.getlist("prospect_ids"):
        text = str(raw).strip()
        if not text:
            continue
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                continue
    # Preserve order, drop dupes.
    seen: set[int] = set()
    ordered: list[int] = []
    for pid in ids:
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered


def _resolve_enrich_targets(session: Session, ids: list[int]) -> list[Prospect]:
    targets: list[Prospect] = []
    for pid in ids:
        prospect = session.get(Prospect, pid)
        if prospect and prospect.status not in CLOSED_STATUSES:
            targets.append(prospect)
        if len(targets) >= ENRICH_TARGET_CAP:
            break
    return targets


def _create_enrich_request(session: Session, targets: list[Prospect]) -> ResearchRequest:
    req = ResearchRequest(
        kind="enrich",
        target_ids=[p.id for p in targets],
        requested_count=len(targets),
    )
    session.add(req)
    session.flush()
    return req


def _run_local_enricher(session: Session, req: ResearchRequest, request: Request):
    """Execute search/scrape, deposit, import; return an HTML results page."""
    from ..enrich import enrich_prospects, write_enrich_deposit
    from ..inbox import sweep_inbox

    print(
        f"[enricher] Starting local web search/scrape for R-{req.id} "
        f"({len(req.target_ids)} prospect(s))…",
        flush=True,
    )
    result = enrich_prospects(
        session,
        list(req.target_ids),
        request_id=req.id,
    )
    path = write_enrich_deposit(result)
    imported = False
    if path:
        sweep_inbox(session)
        imported = True
        print(f"[enricher] Wrote {path.name} and imported.", flush=True)
    else:
        print("[enricher] Finished with no new public facts to deposit.", flush=True)

    with_facts = sum(1 for item in result.items if item.record)
    without_facts = sum(1 for item in result.items if not item.record and item.prospect_id)
    return render(
        request,
        "enrich_result.html",
        req=req,
        items=result.items,
        deposit_path=path.name if path else None,
        imported=imported,
        with_facts=with_facts,
        without_facts=without_facts,
    )


@router.post("/requests/enrich")
async def create_enrich_request(request: Request, session: Session = Depends(get_session)):
    """Create an enrich request and open the Codex-style brief (does NOT scrape)."""
    form = await request.form()
    targets = _resolve_enrich_targets(session, _parse_enrich_ids(form))
    if not targets:
        return RedirectResponse(
            "/prospects?toast=Select+one+or+more+open+prospects+to+enrich",
            status_code=303,
        )
    req = _create_enrich_request(session, targets)
    return RedirectResponse(f"/requests/{req.id}/brief", status_code=303)


@router.get("/requests/enrich/run", response_class=HTMLResponse)
def run_enricher_get_hint(request: Request):
    """Browser address-bar visits are GET; the engine only accepts POST from the UI."""
    return render(request, "enrich_run_hint.html")


@router.post("/requests/enrich/run")
async def run_enricher_now(request: Request, session: Session = Depends(get_session)):
    """Run the local enricher (public web search + scrape) for selected prospects.

    This is the real engine path — it does not open the brief.
    """
    form = await request.form()
    targets = _resolve_enrich_targets(session, _parse_enrich_ids(form))
    if not targets:
        return RedirectResponse(
            "/prospects?toast=Select+one+or+more+open+prospects+to+enrich",
            status_code=303,
        )
    req = _create_enrich_request(session, targets)
    return _run_local_enricher(session, req, request)


@router.post("/requests/{request_id}/run-enricher")
def run_enricher_for_request(
    request_id: int, request: Request, session: Session = Depends(get_session)
):
    """Re-run the local enricher for an existing enrich research request."""
    req = session.get(ResearchRequest, request_id)
    if not req or req.kind != "enrich":
        return RedirectResponse("/import?toast=Not+an+enrich+request", status_code=303)
    return _run_local_enricher(session, req, request)


@router.get("/requests/{request_id}/brief", response_class=PlainTextResponse)
def request_brief(request_id: int, request: Request, session: Session = Depends(get_session)):
    req = session.get(ResearchRequest, request_id)
    if not req:
        return PlainTextResponse("No such research request.", status_code=404)
    icp = get_icp(session)
    if req.kind == "enrich":
        targets = [p for pid in req.target_ids if (p := session.get(Prospect, pid))]
        return build_enrich_brief(icp, req, targets)
    domains = sorted(
        d for d in session.scalars(select(Company.domain)) if d and not d.endswith(".example")
    )
    return build_brief(icp, domains, req, decisions=decision_patterns(session))


@router.post("/requests/{request_id}/close")
def close_request(request_id: int, request: Request, session: Session = Depends(get_session)):
    req = session.get(ResearchRequest, request_id)
    if req and req.status == "open":
        req.status = "closed"
        req.closed_at = utc_now()
    return RedirectResponse("/import?toast=Request+closed", status_code=303)


# ---- import & duplicate review -------------------------------------------


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, session: Session = Depends(get_session)):
    open_requests = [
        {
            "req": req,
            "delivered": delivered_count(session, req),
        }
        for req in session.scalars(
            select(ResearchRequest).where(ResearchRequest.status == "open")
            .order_by(ResearchRequest.created_at.desc())
        )
    ]
    dupes = list(session.scalars(
        select(DupeReview)
        .options(joinedload(DupeReview.existing_prospect).joinedload(Prospect.company))
        .where(DupeReview.status == "pending")
        .order_by(DupeReview.created_at)
    ))
    batches = list(session.scalars(
        select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(15)
    ))
    rejects: list[dict] = []
    if config.REJECTS_PATH.exists():
        lines = config.REJECTS_PATH.read_text(encoding="utf-8").strip().splitlines()
        for line in lines[-10:][::-1]:
            try:
                rejects.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    icp = get_icp(session)
    return render(
        request, "import.html",
        dupes=dupes, batches=batches, rejects=rejects,
        open_requests=open_requests,
        inbox_files=[p.name for p in pending_files()],
        inbox_dir=str(config.INBOX_DIR),
        rejects_path=str(config.REJECTS_PATH),
        icp_ready=icp_is_ready(icp),
        icp_missing=icp_missing(icp),
    )


@router.post("/import/inbox")
def import_inbox(request: Request, session: Session = Depends(get_session)):
    summaries = sweep_inbox(session)
    if not summaries:
        return RedirectResponse("/import?toast=Inbox+is+empty", status_code=303)
    created = sum(s.created for s in summaries)
    parts = [f"{created} new prospect{'s' if created != 1 else ''} imported"]
    review = sum(s.review for s in summaries)
    rejected = sum(s.rejected for s in summaries)
    if review:
        parts.append(f"{review} need duplicate review")
    if rejected:
        parts.append(f"{rejected} rejected")
    from urllib.parse import quote
    return RedirectResponse(f"/import?toast={quote(', '.join(parts))}", status_code=303)


@router.post("/import/csv")
async def import_csv_route(request: Request, file: UploadFile, session: Session = Depends(get_session)):
    content = await file.read()
    summary = ingest_csv(session, content, filename=file.filename or "upload.csv")
    from urllib.parse import quote
    return RedirectResponse(f"/import?toast={quote(summary.one_line())}", status_code=303)


@router.post("/dupes/{review_id}")
def resolve_dupe_route(
    review_id: int,
    request: Request,
    session: Session = Depends(get_session),
    resolution: str = Form(...),
):
    review = session.get(DupeReview, review_id)
    from urllib.parse import quote
    if not review:
        return RedirectResponse("/import?toast=Review+not+found", status_code=303)
    try:
        message = resolve_dupe(session, review, resolution)
    except ValueError as exc:
        message = str(exc)
    return RedirectResponse(f"/import?toast={quote(message)}", status_code=303)


# ---- settings --------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: Session = Depends(get_session)):
    backup = latest_backup()
    icp = get_icp(session)
    return render(
        request, "settings.html",
        icp=icp,
        icp_ready=icp_is_ready(icp),
        icp_missing=icp_missing(icp),
        app_version=config.APP_VERSION,
        crm_home=str(config.CRM_HOME) if config.CRM_HOME else None,
        data_dir=str(config.DATA_DIR),
        db_path=str(config.DB_PATH),
        inbox_dir=str(config.INBOX_DIR),
        backup_name=backup.name if backup else None,
        backup_dir=str(config.BACKUP_DIR),
    )


@router.post("/settings/icp")
def save_icp(
    request: Request,
    session: Session = Depends(get_session),
    product: str = Form(""),
    industry: str = Form(""),
    company_size: str = Form(""),
    regions: str = Form(""),
    region_note: str = Form(""),
    target_titles: str = Form(""),
    adjacent_titles: str = Form(""),
    account_rule: str = Form(""),
    pain_points: str = Form(""),
    notes: str = Form(""),
):
    value = {
        "product": product.strip(),
        "industry": industry.strip(),
        "company_size": company_size.strip(),
        "regions": _lines(regions),
        "region_note": region_note.strip(),
        "target_titles": _lines(target_titles),
        "adjacent_titles": _lines(adjacent_titles),
        "account_rule": account_rule.strip(),
        "pain_points": _lines(pain_points),
        "notes": notes.strip(),
    }
    session.merge(Setting(key="icp", value=value))
    return RedirectResponse("/settings?toast=ICP+saved", status_code=303)


@router.post("/settings/icp/example")
def load_example_icp(session: Session = Depends(get_session)):
    """Fill Settings with the land/homebuilding sample ICP (editable after)."""
    session.merge(Setting(key="icp", value=dict(EXAMPLE_ICP)))
    return RedirectResponse(
        "/settings?toast=Loaded+example+ICP+—+edit+to+match+your+market",
        status_code=303,
    )


@router.post("/settings/backup")
def backup_route(request: Request):
    target = backup_now()
    from urllib.parse import quote
    return RedirectResponse(f"/settings?toast={quote('Backup written: ' + target.name)}", status_code=303)


# ---- board & stats ----------------------------------------------------------


@router.get("/board", response_class=HTMLResponse)
def board(request: Request, session: Session = Depends(get_session)):
    prospects = list(session.scalars(
        select(Prospect).join(Prospect.company).options(joinedload(Prospect.company))
        .order_by(Prospect.priority.desc(), Prospect.icp_score.is_(None), Prospect.icp_score.desc())
    ))
    columns = {slug: [] for slug in STATUSES}
    for p in prospects:
        columns[p.status].append(p)
    return render(request, "board.html", columns=columns)


@router.get("/stats", response_class=HTMLResponse)
def stats(request: Request, session: Session = Depends(get_session)):
    prospects = list(session.scalars(
        select(Prospect).join(Prospect.company).options(joinedload(Prospect.company))
    ))
    by_status = Counter(p.status for p in prospects)
    by_region = Counter((p.region or p.company.region or "Unknown") for p in prospects)
    by_source = Counter(p.source for p in prospects)
    with_phone = sum(1 for p in prospects if p.phone)
    with_email = sum(1 for p in prospects if p.email)
    active = sum(1 for p in prospects if p.status not in CLOSED_STATUSES)
    total = len(prospects)
    return render(
        request, "stats.html",
        total=total, active=active,
        with_phone=with_phone, with_email=with_email,
        by_status=[(slug, label, by_status.get(slug, 0)) for slug, label in STATUSES.items()],
        by_region=by_region.most_common(),
        by_source=by_source.most_common(),
        max_status=max(by_status.values(), default=1),
        max_region=max(by_region.values(), default=1),
    )


# ---- research brief ---------------------------------------------------------


@router.get("/brief", response_class=PlainTextResponse)
def research_brief(request: Request, session: Session = Depends(get_session)):
    icp = get_icp(session)
    domains = sorted(
        d for d in session.scalars(select(Company.domain)) if d and not d.endswith(".example")
    )
    return build_brief(icp, domains, decisions=decision_patterns(session))


def decision_patterns(session: Session) -> dict:
    """Distill Gustavo's vetting decisions so every discovery brief teaches
    the agent what he actually accepts and rejects."""
    rejected = []
    for p in session.scalars(
        select(Prospect).join(Prospect.company).options(joinedload(Prospect.company))
        .where(Prospect.status.in_(CLOSED_STATUSES))
        .order_by(Prospect.updated_at.desc()).limit(10)
    ):
        reason = (p.notes or "").strip()
        if not reason:
            last_status = session.scalar(
                select(Activity.body).where(
                    Activity.prospect_id == p.id, Activity.kind == "status"
                ).order_by(Activity.created_at.desc()).limit(1)
            )
            reason = (last_status or "").strip()
        rejected.append({
            "company": p.company.name,
            "title": p.title or "",
            "reason": reason or "no reason recorded",
        })

    accepted_titles = Counter(
        (p.title or "unknown").strip()
        for p in session.scalars(
            select(Prospect).where(Prospect.status.in_(("queued", "follow_up", "conversation", "meeting")))
        )
    )
    return {"rejected": rejected, "accepted_titles": accepted_titles.most_common(8)}


def build_brief(
    icp: dict,
    known_domains: list[str],
    req: ResearchRequest | None = None,
    decisions: dict | None = None,
) -> str:
    count = req.requested_count if req else 10
    regions = ", ".join(icp["regions"])
    titles = "\n".join(f"- {t}" for t in icp["target_titles"])
    adjacent = "\n".join(f"- {t}" for t in icp["adjacent_titles"])
    pains = "; ".join(icp["pain_points"])
    exclusions = "\n".join(f"- {d}" for d in known_domains) or "- (none yet)"

    learning = ""
    if decisions and (decisions["rejected"] or decisions["accepted_titles"]):
        lines = []
        if decisions["accepted_titles"]:
            accepted = ", ".join(f"{title} ({n})" for title, n in decisions["accepted_titles"])
            lines.append(f"- Titles I have accepted and queued for calling: {accepted}")
        for item in decisions["rejected"]:
            lines.append(
                f"- REJECTED: {item['title'] or 'contact'} at {item['company']} — {item['reason']}"
            )
        learning = (
            "\nLEARN FROM MY DECISIONS (my actual vetting on past batches)\n"
            + "\n".join(lines)
            + "\nDo not bring me more prospects that match my rejection patterns.\n"
        )

    focus = ""
    if req and (req.region_focus or req.title_focus or req.notes):
        lines = []
        if req.region_focus:
            lines.append(f"- Concentrate on: {req.region_focus}")
        if req.title_focus:
            lines.append(f"- Prioritize the title: {req.title_focus}")
        if req.notes:
            lines.append(f"- Extra instructions: {req.notes}")
        focus = "\nFOCUS FOR THIS REQUEST\n" + "\n".join(lines) + "\n"

    if req:
        delivery = f"""HOW TO DELIVER
Follow AGENTS.md at the repo root. In short:
1. Write ONE JSON file matching schemas/prospect-deposit.schema.json into
   inbox/, and set "request_id": {req.id} in the envelope so delivery is
   tracked against this request.
2. Self-check first: python -m crm validate inbox/<your-file>.json
   Fix anything it flags as invalid.
3. Deposit: python -m crm import --inbox
   Confirm the summary shows your records were created (not rejected).
4. If you deliver fewer than {count}, put every concrete reason in the
   envelope's "shortfall_reasons" — do not pad with weak fits instead."""
    else:
        delivery = """HOW TO DELIVER
Follow the deposit instructions in AGENTS.md at the repo root: write one
JSON file matching schemas/prospect-deposit.schema.json into inbox/,
self-check it with `python -m crm validate <file>`, then run
`python -m crm import --inbox` and confirm the import summary shows
your records were created (not rejected)."""

    return f"""Research {count} new cold-call prospects for me.

WHO I AM SELLING: {icp['product']}.

IDEAL CUSTOMER PROFILE
- Industry: {icp['industry']}
- Company size: {icp['company_size']} employees
- Regions: {regions} ({icp['region_note']})
- Account rule: {icp['account_rule']}
- Their pain points: {pains}
- Context: {icp['notes']}
{focus}
{learning}
TARGET TITLES (best first)
{titles}
Adjacent titles that also count:
{adjacent}

SCORING RUBRIC (keep scores comparable between batches)
Start from what you can verify with evidence URLs, not vibes:
- 90–100 — exact target title, verified account type per the account rule,
  in a listed region, company size in band. All four confirmed.
- 75–89 — adjacent title instead of exact, OR exactly one of
  region / size / account-type is unconfirmed (say which in the rationale).
- 60–74 — two attributes unconfirmed, or region is a neighbor of a listed one.
- Below 60 — don't include unless something notable justifies it
  (explain in the rationale).
A published direct phone adds +5 (cap 100). If you can't verify the
account rule, omit the prospect entirely.

RULES
- Web research through your own browsing/search only. No third-party data
  APIs (no Apollo, Clearbit, Hunter, etc.), no logins, no paywalled or
  private sources, no guessing email patterns.
- Every prospect needs at least one evidence URL from a public page that
  verifies the person holds that role at that company (official company
  team page is best). A DIRECT PHONE NUMBER is the single most valuable
  field — always check the company website, press releases, and public
  directories for one.
- At most 3 contacts per company; breadth beats depth for cold calling.
- Skip these companies I already have (by domain):
{exclusions}

{delivery}
"""


def build_enrich_brief(icp: dict, req: ResearchRequest, targets: list[Prospect]) -> str:
    """Stage 2: deep research on already-vetted prospects, using known anchors."""
    blocks = []
    for p in targets:
        have = []
        missing = []
        for label, value in (
            ("direct phone", p.phone), ("email", p.email),
            ("LinkedIn", p.linkedin_url), ("city", p.city),
        ):
            (have if value else missing).append(label)
        known = "; ".join(f"{label}: yes" for label in have) or "nothing beyond the basics"
        evidence = "\n".join(f"    - {link.get('url')}" for link in p.evidence[:4])
        company = p.company.name
        domain = f" ({p.company.domain})" if p.company.domain else ""
        region = p.region or p.company.region or "unknown"
        if p.city:
            city_line = (
                f'- City (REQUIRED search lock): "{p.city}"\n'
                f"  Prefer results tied to this city. Reject same-name people in other cities."
            )
        else:
            city_line = (
                f"- City: UNKNOWN — resolve city first from the company website, "
                f'HQ/office pages, or region "{region}", then lock ALL personal '
                f"searches to that city + full name + company. Write the city into "
                f'the record\'s "city" field once verified.'
            )
        blocks.append(f"""### prospect_id {p.id}: {p.full_name}
SEARCH ANCHORS — use these THREE together on every query; do not broaden:
- Full name: "{p.full_name}"
- Company: "{company}"{domain}
{city_line}
- Title (context only): {p.title or 'unknown'}
- Website: {p.company.website or 'unknown'}
- Region: {region}
- Already have: {known}
- MISSING (find these): {', '.join(missing) or 'nothing critical — focus on rapport intel'}
- Known evidence pages:
{evidence or '    - (none)'}""")

    targets_text = "\n\n".join(blocks)
    return f"""Deep-research these {len(targets)} prospects I have already vetted. Do NOT
find new people — enrich exactly the ones listed.

CONTEXT: I sell {icp['product']}. These are accepted cold-call leads;
your job is to make each one maximally callable.

NARROW THE SEARCH (mandatory)
For each target, every web search must combine:
  1) their full name, 2) their company name, 3) their city
(or the city you just verified if it was missing).
- Do not chase same-name people at other companies or in other cities.
- Company-only news is fine for rapport notes, but personal contact
  data (phone, email, LinkedIn, profiles) must match name + company,
  and city when known.
- If two people share the name, prefer the one tied to the listed
  company (domain/website/evidence), then the listed city.

WHAT TO FIND, IN PRIORITY ORDER
1. DIRECT PHONE NUMBER — the single most valuable field. Company site
   footer/contact pages, press releases, county/public directories,
   chamber-of-commerce listings — always scoped to name + company + city.
2. LinkedIn profile URL + the city they are based in (confirm it matches
   the search lock above).
3. Rapport intel: recent news, project announcements, quotes, conference
   talks, permits/filings involving them or their company. Put this in the
   record's "notes" — it becomes call-prep context.
4. Other PUBLIC profiles (Facebook/Instagram/X business or public pages).
   No logins, so only what is publicly visible — skip anything gated.

RULES
- Public no-login web sources only; no data/enrichment APIs; never guess
  emails or phones — only record what you actually saw published.
- Every new fact needs an evidence URL in the record's "evidence" list.
- Do not change names/titles. If someone changed roles or left, do not
  overwrite anything — describe what you found in "notes".

THE TARGETS
{targets_text}

HOW TO DELIVER
Follow AGENTS.md. Write ONE JSON file into inbox/ with:
- "schema_version": 2, "request_id": {req.id}
- one record per target, each carrying its "prospect_id" from above
  (that is how the update lands on the right person), plus "full_name"
  and "company" repeated exactly as listed — the schema requires them
- new facts in the matching fields (phone, email, linkedin_url, city,
  "profiles": [{{"label": "Facebook", "url": "…"}}]), rapport intel in
  "notes", evidence URLs for everything
Then self-check with `python -m crm validate inbox/<file>.json` (every
record should say "enrich"), deposit with `python -m crm import --inbox`,
and confirm the summary shows records enriched, not rejected. If a field
truly is not publicly findable for someone, say so in "shortfall_reasons".
"""
