"""My Models collection: listing, deletion, GLB export and zip downloads."""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

import gallery
import glb
import render
import settings
from leocad_render import MODEL_SUFFIXES
from paths import safe_join
from store import get_store
from .common import is_pending, model_chat_index, model_info, not_found

router = APIRouter()


@router.get("/api/models")
async def models_list():
    """Every model in data/generated, newest first. Models without a snapshot
    or BOM get them made in the background; poll until
    `pending` is 0."""
    models = gallery.collection(settings.GENERATED_DIR)
    gallery.ensure_artifacts(models)
    index = model_chat_index(get_store())
    items = [{**model_info(path), "chats": index.get(path, [])} for path in models]
    return {"models": items, "pending": sum(1 for i in items if is_pending(i))}


@router.delete("/api/models/{filename}")
async def models_delete(filename: str):
    root = settings.GENERATED_DIR.resolve()
    # Delete only a direct, regular model file. Never follow a link or accept a
    # URL/path supplied by a card.
    model = root / filename
    if (Path(filename).name != filename or filename.startswith(".")
            or model.suffix.lower() not in MODEL_SUFFIXES or model.is_symlink()
            or not model.is_file()):
        not_found("Model not found")
    if model in gallery.publishing or gallery._current == model or render.is_busy(model) or glb.is_converting(model):
        raise HTTPException(409, "This model is still being processed. Try deleting it again when processing finishes.")
    # Notes/previews are shared if car.mpd and car.ldr both exist: retain those.
    shared = any(p != model and p.stem == model.stem for p in gallery.collection(root))
    artifacts = [] if shared else [model.with_suffix(ext) for ext in (".png", ".csv", ".md", ".glb")]
    targets = [model, *artifacts, *glb.cache_files(model)]
    deleted = []
    for path in targets:
        if path.is_file() and not path.is_symlink():
            path.unlink()
            deleted.append(path.name)
    gallery.forget(model)
    # Keep chat records as history. Their existing missing-model state explains
    # that a model is gone, while the agent's original work files remain intact.
    return {"deleted": deleted}


def model_from_url(url: str) -> Optional[Path]:
    """Resolve a /files/generated/ viewer URL to its model file."""
    path = unquote(urlsplit(url).path)
    prefix = "/files/generated/"
    if path.startswith(prefix):
        model = safe_join(settings.GENERATED_DIR, path[len(prefix):])
        if model is not None and model.is_file() and model.suffix.lower() in MODEL_SUFFIXES:
            return model
    return None


@router.get("/api/glb")
async def model_glb(url: str, existing_only: bool = False):
    """Prefer the model's sibling GLB. Otherwise convert and cache it, unless
    the 3D viewer is only checking for an authored alternate (existing_only)."""
    model = model_from_url(url) or not_found("not a model in My Models")
    try:
        if existing_only:
            out = glb.sibling_glb(model) or not_found("no sibling GLB")
        else:
            out = await glb.export_glb(model)
    except glb.GlbError as exc:
        raise HTTPException(500, str(exc)) from None
    return FileResponse(out, media_type="model/gltf-binary", filename=model.stem + ".glb",
                        headers={"Cache-Control": "no-cache"})


def collection_zip(folder: Path, filename: str):
    """Download one collection with its snapshots, BOMs and notes."""
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and not p.name.startswith(".")] if folder.is_dir() else []
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.name)
    tmp.close()
    return FileResponse(tmp.name, filename=filename, media_type="application/zip",
                        background=BackgroundTask(Path(tmp.name).unlink, missing_ok=True))


@router.get("/api/models/zip")
def models_zip():
    return collection_zip(settings.GENERATED_DIR, "generated.zip")

