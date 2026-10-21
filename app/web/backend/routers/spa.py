"""Viewer and player static mounts, and the SPA fallback (must be included last)."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import settings
from paths import safe_join

router = APIRouter()


class RevalidatedStaticFiles(StaticFiles):
    """Static files browsers must check with us before reusing a cached copy (a
    quick 304 while unchanged). Without Cache-Control, browsers reuse one for a
    while without asking, so after an image rebuild a page can run the previous
    version: e.g. an old player's .js and .wasm, whose URLs never change."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def mount_static(app: FastAPI) -> None:
    # Vendored viewer and player libraries (added at build time) and our own pages
    # (viewer.html, player.html) live in separate folders, so development can mount
    # web/viewer/ over the pages.
    if settings.VIEWER_VENDOR_DIR.is_dir():
        app.mount("/viewer/vendor", RevalidatedStaticFiles(directory=settings.VIEWER_VENDOR_DIR), name="viewer-vendor")
    if settings.PLAYER_VENDOR_DIR.is_dir():
        app.mount("/viewer/player-vendor", RevalidatedStaticFiles(directory=settings.PLAYER_VENDOR_DIR),
                  name="player-vendor")
    if settings.VIEWER_DIR.is_dir():
        app.mount("/viewer", RevalidatedStaticFiles(directory=settings.VIEWER_DIR, html=True), name="viewer")
    if (settings.STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=settings.STATIC_DIR / "assets"), name="assets")


RESERVED_PREFIXES = ("api/", "files/", "ldraw/", "ldraw-id/", "viewer/", "assets/")


@router.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str, request: Request):
    if full_path.startswith(RESERVED_PREFIXES):
        return JSONResponse({"detail": "not found"}, status_code=404)
    static = safe_join(settings.STATIC_DIR, full_path) if full_path else None
    if static is not None and static.is_file():
        return FileResponse(static)
    index = settings.STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return PlainTextResponse("Frontend not built: see web/frontend (npm run build).", status_code=404)
