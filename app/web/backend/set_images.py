"""Pictures of owned sets: an uploaded photo, a locally cached copy of the catalogue picture, or a render of the bundled model.

Decided on the set-image issue (#111): the catalogue picture is linked to its source (Rebrickable's CDN) and cached on this
machine for offline use. Cached pictures live under `<DATA_DIR>/cache/set-images/`, are never committed or redistributed, and
are fetched for owned sets unless the owner has switched remote fetching off (on by default, so pictures show without setup). An uploaded picture works for any set,
including third-party ones (CADA), which have no catalogue picture.

Order served: uploaded photo, cached catalogue picture, catalogue picture fetched now (owned LEGO sets only, unless switched off),
render of the bundled model, neutral placeholder. A failed fetch is remembered for a while so a dead link costs one request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

import render
import settings

log = logging.getLogger(__name__)

SET_NUM_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z:._-]{0,39}")
REMOTE_NUM_RE = re.compile(r"[0-9a-z]{1,12}-[0-9]{1,3}")             # a Rebrickable set number; CADA keys ("cada:C51026W") have none
REMOTE_URL = "https://cdn.rebrickable.com/media/sets/{set_num}.jpg"
USER_AGENT = "open-brick-designer (+https://github.com/AlessioNar/open-brick-designer)"
FETCH_TIMEOUT = 10
MAX_BYTES = 2 * 1024 * 1024
FAILURE_MEMORY = 60 * 60
RENDER_SIZE = 384
RENDER_TIMEOUT = 90
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MEDIA_TYPES = {ext: media for media, ext in EXTENSIONS.items()}
PLACEHOLDER = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="192" height="192" role="img" aria-label="No picture for this set">'
               b'<rect x="6" y="6" width="36" height="36" rx="3" fill="none" stroke="#8a8a8a" stroke-width="2"/>'
               b'<circle cx="17" cy="18" r="3" fill="none" stroke="#8a8a8a" stroke-width="2"/>'
               b'<path d="M8 38l10-10 7 7 5-5 10 10" fill="none" stroke="#8a8a8a" stroke-width="2"/></svg>')

_jobs: dict[str, asyncio.Task] = {}
_failed: dict[str, float] = {}


class ImageError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def cache_dir() -> Path:
    return settings.DATA_DIR / "cache" / "set-images"


def settings_file() -> Path:
    return settings.DATA_DIR / "cache" / "set-images-settings.json"


def valid(set_num: str) -> bool:
    return bool(SET_NUM_RE.fullmatch(set_num))


def _stem(set_num: str) -> str:
    return set_num.replace(":", "_").lower()


def image_url(set_num: str) -> str:
    return "/files/set-image/" + quote(set_num, safe="")


# --- the opt-in --------------------------------------------------------------

def remote_enabled() -> bool:
    """Whether the backend may download catalogue pictures. On unless the owner switched it off in Settings or
    LDRAW_NOVA_SET_IMAGE_FETCH is 0/false/no/off (the latter wins, for offline deployments)."""
    env = os.environ.get("LDRAW_NOVA_SET_IMAGE_FETCH", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    try:
        return json.loads(settings_file().read_text(encoding="utf-8")).get("remote_fetch") is not False
    except (OSError, ValueError, AttributeError):
        return True


def set_remote_enabled(value: bool) -> None:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"remote_fetch": bool(value)}), encoding="utf-8")


# --- files -------------------------------------------------------------------

def _find(set_num: str, kind: str) -> Path | None:
    for ext in EXTENSIONS.values():
        path = cache_dir() / f"{_stem(set_num)}.{kind}.{ext}"
        if path.is_file():
            return path
    return None


def uploaded(set_num: str) -> Path | None:
    return _find(set_num, "upload")


def cached(set_num: str) -> Path | None:
    return _find(set_num, "catalog")


def rendered_path(set_num: str) -> Path:
    return cache_dir() / f"{_stem(set_num)}.render.png"


def model_path(set_num: str) -> Path | None:
    """The bundled reference model of a set, if the toolkit ships one."""
    path = settings.TOOLKIT_DIR / "data" / "models-annotated" / f"{set_num}.mpd"
    return path if REMOTE_NUM_RE.fullmatch(set_num.lower()) and path.is_file() else None


def sniff(data: bytes) -> str | None:
    """The media type of a PNG, JPEG or WebP picture by its first bytes, else None. The client's Content-Type is not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _write(set_num: str, kind: str, data: bytes) -> Path:
    media = sniff(data)
    if media is None:
        raise ImageError(415, "The picture must be a PNG, JPEG or WebP image")
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for ext in EXTENSIONS.values():                    # one picture of each kind per set, whatever its type
        (folder / f"{_stem(set_num)}.{kind}.{ext}").unlink(missing_ok=True)
    path = folder / f"{_stem(set_num)}.{kind}.{EXTENSIONS[media]}"
    tmp = folder / f".{path.name}.tmp"
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def save_upload(set_num: str, data: bytes) -> Path:
    if not data:
        raise ImageError(400, "No picture was sent")
    if len(data) > MAX_BYTES:
        raise ImageError(413, f"The picture is larger than {MAX_BYTES // (1024 * 1024)} MB")
    return _write(set_num, "upload", data)


def remove_upload(set_num: str) -> bool:
    path = uploaded(set_num)
    if path is None:
        return False
    path.unlink(missing_ok=True)
    return True


# --- fetching and rendering --------------------------------------------------

def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg,image/png,image/webp"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:      # noqa: S310 - fixed https URL
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ImageError(413, "The picture is too large")
    return data


async def _fetch_job(set_num: str) -> Path | None:
    try:
        data = await asyncio.to_thread(_download, REMOTE_URL.format(set_num=set_num.lower()))
        return _write(set_num, "catalog", data)
    except Exception as exc:                           # offline, 404, wrong type, too large: serve the next fallback
        log.warning("set picture for %s not fetched: %s", set_num, exc)
        _failed["fetch:" + set_num] = time.time()
        return None
    finally:
        _jobs.pop("fetch:" + set_num, None)


async def _render_job(set_num: str, model: Path) -> Path | None:
    out = rendered_path(set_num)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}")
        await render.render(model, tmp, width=RENDER_SIZE, height=RENDER_SIZE, timeout=RENDER_TIMEOUT)
        tmp.replace(out)
        return out
    except Exception as exc:                           # LeoCAD missing, timeout, broken model
        log.warning("render of set %s failed: %s", set_num, render.describe_error(exc))
        _failed["render:" + set_num] = time.time()
        return None
    finally:
        _jobs.pop("render:" + set_num, None)


async def _once(key: str, make, wait: float) -> Path | None:
    if time.time() - _failed.get(key, 0) < FAILURE_MEMORY:
        return None
    job = _jobs.get(key)
    if job is None:
        job = _jobs[key] = asyncio.create_task(make())
    try:
        return await asyncio.wait_for(asyncio.shield(job), wait)
    except asyncio.TimeoutError:
        return None


async def get(set_num: str, owned: bool, wait: float = 20.0) -> Path | None:
    """The best picture for a set, or None for the placeholder. Nothing is downloaded unless the owner opted in and owns the set."""
    for found in (uploaded(set_num), cached(set_num)):
        if found is not None:
            return found
    if owned and remote_enabled() and REMOTE_NUM_RE.fullmatch(set_num.lower()):
        found = await _once("fetch:" + set_num, lambda: _fetch_job(set_num), wait)
        if found is not None:
            return found
    model = model_path(set_num)
    if model is not None:
        if rendered_path(set_num).is_file():
            return rendered_path(set_num)
        return await _once("render:" + set_num, lambda: _render_job(set_num, model), wait)
    return None
