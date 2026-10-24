"""Part thumbnails for the collection: one neutral render per part type, made lazily and cached on disk.

Rendering is software and takes seconds, and the display is shared with every other render (render.py), so a thumbnail is
only rendered when someone looks at the part, at most one at a time, and never twice: `<DATA_DIR>/cache/part-thumbs/<part>.png`.
Until it exists the endpoint answers 202 with a plain placeholder and the page retries; a part that fails to render is
remembered for a while so a broken part does not cost a render per view. The picture is grey on purpose: the colour is
shown next to it as a swatch, which keeps it one render per part instead of one per (part, colour).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

import part_info
import render
import settings

log = logging.getLogger(__name__)

NEUTRAL_COLOUR = 71                      # light bluish grey
SIZE = 192
RENDER_TIMEOUT = 60
FAILURE_MEMORY = 10 * 60
PART_RE = re.compile(r"[0-9a-z][0-9a-z_-]{0,39}")
PLACEHOLDER = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="96" height="96" role="img" aria-label="Part picture not ready">'
               b'<rect x="9" y="22" width="30" height="16" rx="2" fill="none" stroke="#8a8a8a" stroke-width="2"/>'
               b'<rect x="14" y="16" width="8" height="6" rx="1" fill="none" stroke="#8a8a8a" stroke-width="2"/>'
               b'<rect x="26" y="16" width="8" height="6" rx="1" fill="none" stroke="#8a8a8a" stroke-width="2"/></svg>')

_jobs: dict[str, asyncio.Task] = {}
_failed: dict[str, float] = {}


def cache_dir() -> Path:
    return settings.DATA_DIR / "cache" / "part-thumbs"


def thumb_path(part: str) -> Path:
    return cache_dir() / f"{part}.png"


def valid(part: str) -> bool:
    known = part_info.meta()
    return bool(PART_RE.fullmatch(part)) and (not known or part in known)


async def _render(part: str) -> Path:
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / f".{part}.ldr"
    out = thumb_path(part)
    tmp = folder / f".{part}.png"
    source.write_text(f"0 Thumbnail {part}\n0 Name: {part}.ldr\n1 {NEUTRAL_COLOUR} 0 0 0 1 0 0 0 1 0 0 0 1 {part}.dat\n", encoding="utf-8")
    try:
        await render.render(source, tmp, width=SIZE, height=SIZE, timeout=RENDER_TIMEOUT)
        tmp.replace(out)
    finally:
        source.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return out


async def _job(part: str) -> Path | None:
    try:
        return await _render(part)
    except Exception as exc:                 # leocad missing, part without geometry, timeout: fall back to the placeholder
        log.warning("thumbnail for %s failed: %s", part, render.describe_error(exc))
        _failed[part] = time.time()
        return None
    finally:
        _jobs.pop(part, None)


async def get(part: str, wait: float = 20.0) -> Path | None:
    """The cached thumbnail, rendering it when needed; None when it is not ready within `wait` seconds or failed."""
    out = thumb_path(part)
    if out.is_file():
        return out
    if time.time() - _failed.get(part, 0) < FAILURE_MEMORY:
        return None
    job = _jobs.get(part)
    if job is None:
        job = _jobs[part] = asyncio.create_task(_job(part))
    try:
        return await asyncio.wait_for(asyncio.shield(job), wait)
    except asyncio.TimeoutError:
        return None


def status(part: str) -> str:
    if thumb_path(part).is_file():
        return "ready"
    if part in _jobs:
        return "pending"
    return "failed" if time.time() - _failed.get(part, 0) < FAILURE_MEMORY else "missing"
