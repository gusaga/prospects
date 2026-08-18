"""Choose or create a Documents calling-list home before using the CRM."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config
from ..home import (
    attach_home,
    describe_home,
    folder_name_from_label,
    list_homes,
    resolve_listed_home,
)

router = APIRouter()


def _current_name() -> str | None:
    if not config.CRM_HOME:
        return None
    return config.CRM_HOME.name


def _render_picker(request: Request, error: str = "", name_value: str = "") -> HTMLResponse:
    homes = list_homes()
    current = _current_name()
    ready = bool(getattr(request.app.state, "home_ready", False))
    return request.app.state.templates.TemplateResponse(
        request,
        "homes.html",
        {
            "request": request,
            "homes": homes,
            "error": error,
            "name_value": name_value,
            "current_name": current if ready else None,
            "home_ready": ready,
            "homes_parent": str(config.HOMES_PARENT),
        },
    )


@router.get("/homes", response_class=HTMLResponse)
def pick_home(request: Request):
    return _render_picker(request)


@router.post("/homes/open")
def open_home(request: Request, name: str = Form("")):
    try:
        home = resolve_listed_home(name)
    except ValueError as exc:
        return _render_picker(request, error=str(exc))
    if not home.is_dir():
        return _render_picker(
            request,
            error="That list folder is not on disk yet. Start a new one instead.",
        )
    attach_home(request.app, home)
    return RedirectResponse("/", status_code=303)


@router.post("/homes/new")
def new_home(request: Request, name: str = Form("")):
    try:
        folder = folder_name_from_label(name)
        home = resolve_listed_home(folder)
    except ValueError as exc:
        return _render_picker(request, error=str(exc), name_value=name)

    if home.is_dir() and (home / "data" / "crm.db").is_file():
        label = describe_home(home)["label"]
        return _render_picker(
            request,
            error=f"“{label}” already exists. Open it from the list above.",
            name_value=name,
        )
    attach_home(request.app, home)
    return RedirectResponse("/settings?toast=New+list+created.+Fill+who+you+sell+to.", status_code=303)
