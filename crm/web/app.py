"""The FastAPI application: server-rendered pages, small JSON endpoints for
inline editing, everything bound to localhost."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from .. import config
from ..db import build_session_factory, create_db_engine, initialize
from ..dedupe import name_key, normalize_domain
from ..graph import build_atlas_graph, build_company_graph
from ..inbox import pending_files
from ..models import (
    CLOSED_STATUSES,
    PRIORITIES,
    STATUSES,
    Activity,
    Company,
    Prospect,
    ResearchRequest,
)
from .queries import enriched_prospect_ids, parse_filters, prospect_query, region_options
from .routes_data import delivered_count, get_icp, icp_is_ready, icp_missing

WEB_DIR = Path(__file__).resolve().parent

EDITABLE_PROSPECT_FIELDS = {
    "full_name", "title", "phone", "email", "linkedin_url", "region", "city",
    "icp_score", "icp_rationale", "notes", "priority", "status", "next_followup_on",
}
EDITABLE_COMPANY_FIELDS = {"name", "domain", "website", "industry", "size_band", "region"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- template helpers

def fmt_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.astimezone().date() if value.tzinfo else value.date()
    return value.strftime("%b %d, %Y")


def due_label(value: date | None) -> str:
    if not value:
        return ""
    delta = (value - date.today()).days
    if delta < -1:
        return f"{-delta} days overdue"
    if delta == -1:
        return "Yesterday"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return value.strftime("%b %d")


def due_class(value: date | None) -> str:
    if not value:
        return ""
    delta = (value - date.today()).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "due-today"
    return "upcoming"


def display_region(prospect: Prospect) -> str:
    return prospect.region or prospect.company.region or ""


def current_home_label() -> str:
    if config.CRM_HOME:
        from ..home import display_name

        return display_name(config.CRM_HOME)
    return "This folder"


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    env = templates.env
    env.globals.update(
        STATUSES=STATUSES,
        PRIORITIES=PRIORITIES,
        CLOSED_STATUSES=CLOSED_STATUSES,
        today=date.today,
        current_home_label=current_home_label,
    )
    env.filters.update(
        fmt_date=fmt_date,
        due_label=due_label,
        due_class=due_class,
        region_of=display_region,
    )
    return templates


# ---------------------------------------------------------------- actions

def apply_action(session: Session, prospect: Prospect, action: str, note: str, when: str) -> str:
    """One-click call outcomes and status moves. Returns a toast message."""
    note = (note or "").strip()
    followup = date.fromisoformat(when) if when else None
    now = utc_now()

    def log(kind: str, body: str) -> None:
        session.add(Activity(prospect_id=prospect.id, kind=kind, body=body))

    if action == "no_answer":
        prospect.status = "no_answer"
        prospect.last_contacted_at = now
        prospect.next_followup_on = followup or (date.today() + timedelta(days=config.NO_ANSWER_RETRY_DAYS))
        log("call", f"Call — no answer.{' ' + note if note else ''} Retry {due_label(prospect.next_followup_on)}.")
        return f"Logged: no answer. Retry {due_label(prospect.next_followup_on)}."
    if action == "conversation":
        prospect.status = "conversation"
        prospect.last_contacted_at = now
        if followup:
            prospect.next_followup_on = followup
        log("call", f"Call — had a conversation.{' ' + note if note else ''}")
        return "Logged: conversation."
    if action == "queue":
        prospect.status = "queued"
        log("status", f"Queued for calling.{' ' + note if note else ''}")
        return "Moved to the call queue."
    if action == "follow_up":
        prospect.status = "follow_up"
        prospect.next_followup_on = followup or (date.today() + timedelta(days=3))
        log("status", f"Follow-up scheduled for {due_label(prospect.next_followup_on)}.{' ' + note if note else ''}")
        return f"Follow-up set for {due_label(prospect.next_followup_on)}."
    if action == "meeting":
        prospect.status = "meeting"
        if followup:
            prospect.next_followup_on = followup
        log("status", f"Meeting booked!{' ' + note if note else ''}")
        return "Meeting booked 🎉"
    if action == "not_fit":
        prospect.status = "not_fit"
        prospect.next_followup_on = None
        log("status", f"Marked not a fit.{' ' + note if note else ''}")
        return "Marked: not a fit."
    if action == "do_not_contact":
        prospect.status = "do_not_contact"
        prospect.next_followup_on = None
        log("status", f"Marked do-not-contact.{' ' + note if note else ''}")
        return "Marked: do not contact."
    if action == "set_followup" and followup:
        prospect.next_followup_on = followup
        log("status", f"Next follow-up set for {due_label(followup)}.")
        return f"Follow-up set for {due_label(followup)}."
    raise ValueError(f"Unknown action {action!r}")


# ---------------------------------------------------------------- app factory

def create_app(*, pick_home: bool = False) -> FastAPI:
    templates = make_templates()

    app = FastAPI(title="Prospecting CRM", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.state.templates = templates
    app.state.engine = None
    app.state.factory = None
    app.state.home_ready = False
    app.state.pick_home = pick_home

    if not pick_home:
        engine = create_db_engine()
        initialize(engine)
        app.state.engine = engine
        app.state.factory = build_session_factory(engine)
        app.state.home_ready = True

    from .routes_data import router as data_router
    from .routes_homes import router as homes_router

    app.include_router(homes_router)
    app.include_router(data_router)

    @app.middleware("http")
    async def require_home(request: Request, call_next):
        if app.state.home_ready:
            return await call_next(request)
        path = request.url.path
        if (
            path.startswith("/static/")
            or path in {"/homes", "/homes/open", "/homes/new"}
        ):
            return await call_next(request)
        return RedirectResponse("/homes", status_code=303)

    def get_session():
        factory = app.state.factory
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def render(name: str, request: Request, **context) -> HTMLResponse:
        context.setdefault("request", request)
        return templates.TemplateResponse(request, name, context)

    def back(request: Request, toast: str = "") -> RedirectResponse:
        target = request.headers.get("referer") or "/"
        if toast:
            separator = "&" if "?" in target else "?"
            target = f"{target.split('#')[0]}{separator}{urlencode({'toast': toast})}"
        return RedirectResponse(target, status_code=303)

    # ---- Today ----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def today_view(request: Request, session: Session = Depends(get_session)):
        base = select(Prospect).join(Prospect.company).options(joinedload(Prospect.company))
        order = (Prospect.priority.desc(), Prospect.icp_score.is_(None), Prospect.icp_score.desc())
        due = list(session.scalars(
            base.where(
                Prospect.next_followup_on.is_not(None),
                Prospect.next_followup_on <= date.today(),
                Prospect.status.not_in(CLOSED_STATUSES),
            ).order_by(Prospect.next_followup_on.asc(), *order)
        ))
        due_ids = {p.id for p in due}
        queued = [p for p in session.scalars(
            base.where(Prospect.status == "queued").order_by(*order)
        ) if p.id not in due_ids]
        called_today = session.scalar(
            select(func.count(Activity.id)).where(
                Activity.kind == "call",
                Activity.created_at >= datetime.combine(date.today(), datetime.min.time()).astimezone(timezone.utc),
            )
        ) or 0
        new_count = session.scalar(
            select(func.count(Prospect.id)).where(Prospect.status == "new")
        ) or 0
        open_requests = []
        for req in session.scalars(
            select(ResearchRequest)
            .where(ResearchRequest.status == "open")
            .order_by(ResearchRequest.created_at.desc())
        ):
            delivered = delivered_count(session, req)
            pct = (
                min(100, int(round(100 * delivered / req.requested_count)))
                if req.requested_count
                else 0
            )
            open_requests.append({"req": req, "delivered": delivered, "pct": pct})
        icp = get_icp(session)
        return render(
            "today.html",
            request,
            due=due,
            queued=queued,
            called_today=called_today,
            new_count=new_count,
            open_requests=open_requests,
            inbox_count=len(pending_files()),
            icp_ready=icp_is_ready(icp),
            icp_missing=icp_missing(icp),
        )

    # ---- Prospect list --------------------------------------------------

    @app.get("/prospects", response_class=HTMLResponse)
    def prospect_list(request: Request, session: Session = Depends(get_session)):
        f = parse_filters(request)
        prospects = list(session.scalars(prospect_query(session, f)).unique())
        enriched_ids = enriched_prospect_ids(session, [p.id for p in prospects])
        context = dict(
            prospects=prospects,
            f=f,
            regions=region_options(session),
            count=len(prospects),
            query_string=request.url.query,
            enriched_ids=enriched_ids,
        )
        if request.query_params.get("partial"):
            return render("_table.html", request, **context)
        return render("prospects.html", request, **context)

    @app.get("/export.csv")
    def export_csv(request: Request, session: Session = Depends(get_session)):
        f = parse_filters(request)
        prospects = session.scalars(prospect_query(session, f)).unique()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow([
            "company", "company_domain", "full_name", "title", "phone", "email",
            "linkedin_url", "region", "industry", "size_band", "icp_score",
            "icp_rationale", "status", "priority", "notes", "next_followup_on",
            "last_contacted_at", "date_added", "evidence_urls",
        ])
        for p in prospects:
            writer.writerow([
                p.company.name, p.company.domain or "", p.full_name, p.title or "",
                p.phone or "", p.email or "", p.linkedin_url or "", display_region(p),
                p.company.industry or "", p.company.size_band or "",
                p.icp_score if p.icp_score is not None else "", p.icp_rationale or "",
                p.status, p.priority, p.notes or "",
                p.next_followup_on.isoformat() if p.next_followup_on else "",
                p.last_contacted_at.isoformat() if p.last_contacted_at else "",
                p.created_at.date().isoformat() if p.created_at else "",
                "|".join(link.get("url", "") for link in p.evidence),
            ])
        filename = f"prospects-{date.today().isoformat()}.csv"
        return Response(
            stream.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- Manual add ------------------------------------------------------

    @app.get("/prospects/new", response_class=HTMLResponse)
    def new_prospect_form(request: Request, session: Session = Depends(get_session)):
        companies = list(session.scalars(select(Company).order_by(Company.name)))
        return render("prospect_new.html", request, companies=companies)

    @app.post("/prospects/new")
    def create_prospect(
        request: Request,
        session: Session = Depends(get_session),
        company_name: str = Form(""),
        company_domain: str = Form(""),
        full_name: str = Form(...),
        title: str = Form(""),
        phone: str = Form(""),
        email: str = Form(""),
        linkedin_url: str = Form(""),
        region: str = Form(""),
        icp_score: str = Form(""),
        icp_rationale: str = Form(""),
        notes: str = Form(""),
        status: str = Form("new"),
        priority: int = Form(2),
    ):
        from ..ingest import ingest_records

        record = {
            "company": {"name": company_name.strip() or "(no company)",
                        "domain": company_domain.strip() or None,
                        "region": region.strip() or None},
            "full_name": full_name, "title": title or None, "phone": phone or None,
            "email": email or None, "linkedin_url": linkedin_url or None,
            "region": region or None, "icp_rationale": icp_rationale or None,
            "notes": notes or None, "status": status, "priority": priority,
        }
        if icp_score.strip():
            record["icp_score"] = icp_score.strip()
        summary = ingest_records(session, [record], filename="manual entry", source="manual")
        session.flush()
        domain = normalize_domain(company_domain) or None
        company = session.scalar(select(Company).where(
            Company.domain == domain if domain else func.lower(Company.name) == company_name.strip().lower()
        ))
        created = None
        if company:
            created = session.scalar(select(Prospect).where(
                Prospect.company_id == company.id, Prospect.name_key == name_key(full_name)
            ))
        if summary.created and created:
            return RedirectResponse(f"/prospects/{created.id}?toast=Prospect+added", status_code=303)
        if summary.review:
            return RedirectResponse("/import?toast=Looks+like+a+near-duplicate+—+check+the+review+queue", status_code=303)
        if (summary.duplicates or summary.enriched) and created:
            return RedirectResponse(f"/prospects/{created.id}?toast=Already+existed+—+showing+the+existing+record", status_code=303)
        return RedirectResponse("/prospects?toast=Could+not+add+—+see+data/rejects.jsonl", status_code=303)

    # ---- Detail ----------------------------------------------------------

    @app.get("/prospects/{prospect_id}", response_class=HTMLResponse)
    def prospect_detail(prospect_id: int, request: Request, session: Session = Depends(get_session)):
        prospect = session.get(Prospect, prospect_id, options=[joinedload(Prospect.company)])
        if not prospect:
            return render("missing.html", request, thing="prospect")
        colleagues = [p for p in prospect.company.prospects if p.id != prospect.id]
        activities = list(session.scalars(
            select(Activity).where(Activity.prospect_id == prospect.id).order_by(Activity.created_at.desc())
        ))
        return render("prospect_detail.html", request, p=prospect, colleagues=colleagues, activities=activities)

    @app.post("/prospects/{prospect_id}/action")
    def prospect_action(
        prospect_id: int,
        request: Request,
        session: Session = Depends(get_session),
        action: str = Form(...),
        note: str = Form(""),
        when: str = Form(""),
    ):
        prospect = session.get(Prospect, prospect_id)
        if not prospect:
            return back(request, "That prospect no longer exists")
        try:
            toast = apply_action(session, prospect, action, note, when)
        except ValueError as exc:
            toast = str(exc)
        return back(request, toast)

    @app.post("/prospects/{prospect_id}/notes")
    def add_note(
        prospect_id: int,
        request: Request,
        session: Session = Depends(get_session),
        body: str = Form(...),
    ):
        body = body.strip()
        if body:
            session.add(Activity(prospect_id=prospect_id, kind="note", body=body))
        return back(request, "Note added" if body else "")

    @app.post("/prospects/{prospect_id}/evidence")
    def add_evidence(
        prospect_id: int,
        request: Request,
        session: Session = Depends(get_session),
        url: str = Form(...),
        note: str = Form(""),
    ):
        prospect = session.get(Prospect, prospect_id)
        if prospect and url.startswith(("http://", "https://")):
            prospect.evidence = prospect.evidence + [{"url": url.strip(), "note": note.strip() or None}]
            return back(request, "Evidence link added")
        return back(request, "Evidence links must start with http:// or https://")

    @app.post("/prospects/{prospect_id}/evidence/remove")
    def remove_evidence(
        prospect_id: int,
        request: Request,
        session: Session = Depends(get_session),
        index: int = Form(...),
    ):
        prospect = session.get(Prospect, prospect_id)
        if prospect and 0 <= index < len(prospect.evidence):
            links = list(prospect.evidence)
            links.pop(index)
            prospect.evidence = links
        return back(request, "Evidence link removed")

    @app.get("/photos/{prospect_id}")
    def serve_photo(prospect_id: int, session: Session = Depends(get_session)):
        from fastapi.responses import FileResponse

        from ..photos import absolute_photo_path

        prospect = session.get(Prospect, prospect_id)
        path = absolute_photo_path(prospect.photo_path) if prospect else None
        if not path:
            return Response(status_code=404)
        media = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        }.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media)

    @app.post("/prospects/{prospect_id}/photo")
    async def save_photo(
        prospect_id: int,
        request: Request,
        session: Session = Depends(get_session),
        photo_url: str = Form(""),
        clear: str = Form(""),
        photo_file: UploadFile | None = File(None),
    ):
        from ..photos import download_photo, save_photo_bytes

        prospect = session.get(Prospect, prospect_id)
        if not prospect:
            return back(request, "Prospect not found")
        if clear in ("1", "true", "on", "yes"):
            if prospect.photo_path:
                from ..photos import absolute_photo_path

                old = absolute_photo_path(prospect.photo_path)
                if old and old.is_file():
                    try:
                        old.unlink()
                    except OSError:
                        pass
            prospect.photo_path = None
            session.add(Activity(prospect_id=prospect.id, kind="system", body="Removed prospect photo"))
            return back(request, "Photo removed")

        saved = None
        if photo_file and photo_file.filename:
            data = await photo_file.read()
            saved = save_photo_bytes(prospect.id, data, photo_file.content_type)
        elif photo_url.strip():
            saved = download_photo(photo_url.strip(), prospect.id)
        if not saved:
            return back(request, "Could not save photo — use a public image URL or JPG/PNG upload")
        prospect.photo_path = saved
        session.add(Activity(prospect_id=prospect.id, kind="system", body="Saved prospect photo"))
        return back(request, "Photo saved")

    # ---- Inline editing (JSON) ------------------------------------------

    @app.get("/api/graph")
    def api_graph(
        session: Session = Depends(get_session),
        scope: str = "company",
        company_id: int | None = None,
        focus_prospect_id: int | None = None,
        region: str | None = None,
        status: str | None = None,
    ):
        """JSON for BDR network maps (company spider or atlas account list)."""
        titles = get_icp(session).get("target_titles") or []
        if scope == "atlas":
            return build_atlas_graph(
                session,
                region=region or None,
                status=status or None,
                target_titles=titles,
            )
        if not company_id:
            return JSONResponse(
                {"ok": False, "error": "company_id required unless scope=atlas"},
                status_code=400,
            )
        return build_company_graph(
            session,
            company_id,
            focus_prospect_id=focus_prospect_id,
            target_titles=titles,
        )

    @app.get("/network", response_class=HTMLResponse)
    def network_atlas(request: Request, session: Session = Depends(get_session)):
        """CRM-wide account coverage atlas for BDRs."""
        regions = region_options(session)
        return render(
            "network.html",
            request,
            regions=regions,
            statuses=STATUSES,
            closed_statuses=CLOSED_STATUSES,
        )

    @app.patch("/api/prospects/{prospect_id}")
    async def patch_prospect(prospect_id: int, request: Request, session: Session = Depends(get_session)):
        prospect = session.get(Prospect, prospect_id)
        if not prospect:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        payload = await request.json()
        field = payload.get("field")
        raw = (payload.get("value") or "").strip() if isinstance(payload.get("value"), str) else payload.get("value")
        if field not in EDITABLE_PROSPECT_FIELDS:
            return JSONResponse({"ok": False, "error": f"field {field!r} is not editable"}, status_code=400)
        try:
            value = _coerce_prospect_value(field, raw)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        old = getattr(prospect, field)
        setattr(prospect, field, value)
        if field == "full_name" and value:
            prospect.name_key = name_key(value)
        if field == "status" and old != value:
            session.add(Activity(prospect_id=prospect.id, kind="status",
                                 body=f"Status changed: {STATUSES.get(old, old)} → {STATUSES.get(value, value)}"))
        return {"ok": True, "field": field, "display": _display_value(prospect, field)}

    @app.patch("/api/companies/{company_id}")
    async def patch_company(company_id: int, request: Request, session: Session = Depends(get_session)):
        company = session.get(Company, company_id)
        if not company:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        payload = await request.json()
        field = payload.get("field")
        raw = (payload.get("value") or "").strip()
        if field not in EDITABLE_COMPANY_FIELDS:
            return JSONResponse({"ok": False, "error": f"field {field!r} is not editable"}, status_code=400)
        if field == "name" and not raw:
            return JSONResponse({"ok": False, "error": "company name cannot be empty"}, status_code=400)
        if field == "domain":
            raw = normalize_domain(raw)
        setattr(company, field, raw or None)
        return {"ok": True, "field": field}

    return app


def _coerce_prospect_value(field: str, raw):
    if field == "priority":
        value = int(raw)
        if value not in PRIORITIES:
            raise ValueError("priority must be 1, 2 or 3")
        return value
    if field == "icp_score":
        if raw in (None, ""):
            return None
        value = int(raw)
        if not 0 <= value <= 100:
            raise ValueError("score must be 0–100")
        return value
    if field == "status":
        if raw not in STATUSES:
            raise ValueError("unknown status")
        return raw
    if field == "next_followup_on":
        return date.fromisoformat(raw) if raw else None
    if field == "full_name" and not raw:
        raise ValueError("name cannot be empty")
    return raw or None


def _display_value(prospect: Prospect, field: str) -> str:
    value = getattr(prospect, field)
    if field == "next_followup_on":
        return due_label(value)
    if field == "status":
        return STATUSES.get(value, value)
    if field == "priority":
        return PRIORITIES.get(value, str(value))
    return "" if value is None else str(value)
