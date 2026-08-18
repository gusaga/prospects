"""Download and store prospect headshots locally (offline-friendly)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import config

USER_AGENT = "ProspectingCRM-Photos/1.0 (+local)"
MAX_BYTES = 2_500_000
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def photos_dir() -> Path:
    path = config.DATA_DIR / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def photo_file_for(prospect_id: int, suffix: str = ".jpg") -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "", suffix) or ".jpg"
    if not safe.startswith("."):
        safe = "." + safe
    return photos_dir() / f"{int(prospect_id)}{safe}"


def relative_photo_path(path: Path) -> str:
    """Store paths relative to DATA_DIR, e.g. photos/12.jpg."""
    try:
        return path.resolve().relative_to(config.DATA_DIR.resolve()).as_posix()
    except ValueError:
        return f"photos/{path.name}"


def absolute_photo_path(relative: str | None) -> Path | None:
    if not relative:
        return None
    # Only allow files inside DATA_DIR/photos/
    candidate = (config.DATA_DIR / relative).resolve()
    root = photos_dir().resolve()
    if root not in candidate.parents and candidate.parent != root:
        return None
    if not candidate.is_file():
        return None
    return candidate


def save_photo_bytes(prospect_id: int, data: bytes, content_type: str | None = None) -> str | None:
    if not data or len(data) > MAX_BYTES:
        return None
    ctype = (content_type or "").split(";")[0].strip().lower()
    suffix = ALLOWED_TYPES.get(ctype)
    if not suffix:
        # sniff from magic bytes
        if data[:3] == b"\xff\xd8\xff":
            suffix = ".jpg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            suffix = ".png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            suffix = ".webp"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            suffix = ".gif"
        else:
            return None
    # Remove older extensions for this prospect
    for old in photos_dir().glob(f"{int(prospect_id)}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    dest = photo_file_for(prospect_id, suffix)
    dest.write_bytes(data)
    return relative_photo_path(dest)


def download_photo(url: str, prospect_id: int, *, client: httpx.Client | None = None) -> str | None:
    """Fetch a public image URL and store it under data/photos/."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    # Skip obvious non-photo / tracking pixels
    low = url.lower()
    if any(x in low for x in ("1x1", "pixel", "spacer", "favicon", ".svg")):
        return None
    owns = client is None
    client = client or httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*"},
        timeout=httpx.Timeout(15.0, connect=8.0),
        follow_redirects=True,
    )
    try:
        response = client.get(url)
        if response.status_code >= 400:
            return None
        ctype = response.headers.get("content-type", "")
        return save_photo_bytes(prospect_id, response.content, ctype)
    except httpx.HTTPError:
        return None
    finally:
        if owns:
            client.close()


def guess_image_urls_from_html(html: str, page_url: str, full_name: str) -> list[str]:
    """Find likely headshot URLs near the person's name on a public page."""
    from html import unescape
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    name_bits = [b.lower() for b in full_name.split() if len(b) > 2]
    last = name_bits[-1] if name_bits else ""
    scored: list[tuple[int, str]] = []

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src.startswith("data:"):
            continue
        abs_url = urljoin(page_url, unescape(src))
        if not abs_url.startswith("http"):
            continue
        blob = " ".join(
            [
                src,
                img.get("alt") or "",
                img.get("title") or "",
                " ".join(img.get("class") or []),
                img.parent.get_text(" ", strip=True)[:200] if img.parent else "",
            ]
        ).lower()
        score = 0
        if last and last in blob:
            score += 5
        if name_bits and all(b in blob for b in name_bits):
            score += 4
        if any(k in blob for k in ("headshot", "portrait", "team", "bio", "about", "profile", "staff")):
            score += 2
        if any(k in blob for k in ("logo", "icon", "sprite", "banner", "hero-bg")):
            score -= 4
        width = img.get("width")
        try:
            if width and int(str(width).replace("px", "")) < 40:
                score -= 3
        except ValueError:
            pass
        if score >= 4:
            scored.append((score, abs_url))

    # og:image as weak fallback when name appears on page
    page_text = soup.get_text(" ", strip=True).lower()
    if last and last in page_text:
        og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if og and og.get("content"):
            scored.append((3, urljoin(page_url, og["content"])))

    scored.sort(key=lambda item: -item[0])
    urls: list[str] = []
    for _, url in scored:
        if url not in urls:
            urls.append(url)
        if len(urls) >= 5:
            break
    return urls
