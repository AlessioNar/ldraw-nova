"""File routes: generated/chat files and the LDraw library."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse

import settings
from paths import safe_join
from .common import file_url, not_found

router = APIRouter()


def _serve(root: Path, path: str, *, download: bool = False, case_insensitive: bool = False,
           cache: str = "no-cache", media_type: Optional[str] = None,
           headers: Optional[dict[str, str]] = None) -> FileResponse:
    file = safe_join(root, path, case_insensitive=case_insensitive)
    if file is None or not file.is_file():
        not_found()
    if media_type is None and file.suffix.lower() in (".dat", ".ldr", ".mpd"):
        media_type = "text/plain; charset=utf-8"
    elif media_type is None and file.suffix.lower() == ".md":
        media_type = "text/markdown; charset=utf-8"
    return FileResponse(file, media_type=media_type, headers={"Cache-Control": cache, **(headers or {})},
                        filename=file.name if download else None)


@router.get("/files/{path:path}")
def files(path: str, download: bool = False):
    """Files in data/generated and data/chats. Checked on the resolved path, so
    generated/../output/... can't reach the agents' work folders."""
    file = safe_join(settings.DATA_DIR, path)
    if file is None or file_url(file) is None:
        not_found()
    return _serve(settings.DATA_DIR, path, download=download)


LIBRARY_CACHE = "public, max-age=31536000, immutable"   # baked into the image, never changes


@router.get("/ldraw/{path:path}")
def ldraw_library(path: str):
    return _serve(settings.LDRAW_DIR, path, case_insensitive=True, cache=LIBRARY_CACHE)


@router.get("/ldraw-id/{part_id:path}")
def ldraw_by_id(part_id: str):
    """A type-1 reference as LDraw resolves it: parts/, then p/, then models/.
    One request per part for the viewer and player instead of probing each
    folder (404s). X-LDraw-Folder says which folder it came from: the player
    treats p/ files as primitives."""
    for sub in ("parts", "p", "models"):
        file = safe_join(settings.LDRAW_DIR / sub, part_id, case_insensitive=True)
        if file is not None and file.is_file():
            return _serve(settings.LDRAW_DIR / sub, part_id, case_insensitive=True, cache=LIBRARY_CACHE,
                          headers={"X-LDraw-Folder": sub})
    not_found()
