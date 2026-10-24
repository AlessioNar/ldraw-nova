"""Builds: create, list, open, rename; generate a version from a generator; import a model file; attach a chat.

A build is a model project with versions (see builds.py). The 3D editor serves a build through
/api/builds/{id}/editor (routers/editor.py), the chat through /api/builds/{id}/chat (a chat stores build_id).
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import build_worker
import builds
import gallery
import run_state
import sandbox
import settings
from store import get_store
from .common import not_found
from .editor import _session, _view, edit_model_file, open_session

router = APIRouter()
MAX_IMPORT_BYTES = 16 * 1024 * 1024
PREVIEW_KEEP_SECONDS = 6 * 3600
_generators: dict[str, Any] = {}


async def _worker(payload: dict) -> dict:
    try:
        reply = await build_worker.get_worker().request(payload)
    except build_worker.WorkerBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    except build_worker.WorkerTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from None
    except build_worker.WorkerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    if reply.get("error"):
        raise HTTPException(status_code=422, detail=reply["error"])
    return reply


def _check(store, build_id: str) -> dict:
    if not builds.valid(store, build_id):
        not_found("no such build")
    return builds.get(store, build_id) or not_found("no such build")


def _detail(store, slug: str) -> dict:
    record = builds.get(store, slug) or not_found("no such build")
    index = builds.Index(store)
    rows = builds.version_rows(store, slug, index=index)
    gallery.ensure_artifacts(r_path for r_path in (settings.GENERATED_DIR / r["file"] for r in rows[-3:]) if r_path.is_file())
    chats = [{"id": c["id"], "title": c["title"], "updated_at": c["updated_at"], "running": run_state.is_running(c["id"]),
              "attached": c.get("build_id") == slug} for c in index.chats_of(slug)]
    source = dict(record.get("source") or {"kind": "empty"})
    if source["kind"] == "empty" and rows:
        source["kind"] = rows[0]["origin"]
    summary = builds.summary(store, slug, {**record, "source": source}, index, builds.group_versions())
    latest = rows[-1] if rows else None
    return {"build": summary, "versions": rows, "chats": chats, "source": source,
            "settings_open": bool(latest) and source.get("generator") is not None,
            "latest_from_generator": bool(latest) and latest["origin"] == "generator",
            "pending": any(r["status"] in ("queued", "rendering") or r["bom_status"] in ("queued", "rendering") for r in rows)}


@router.get("/api/builds")
def builds_list():  # plain def: FastAPI runs it in a thread, so its file I/O never stalls other requests
    store = get_store()
    index, groups = builds.Index(store), builds.group_versions()
    items = []
    for slug in builds.all_slugs(store):
        record = builds.get(store, slug, index)
        if record is None:
            continue
        item = builds.summary(store, slug, record, index, groups)
        if item["source"]["kind"] == "empty" and item["latest"]:
            item["source"] = {**item["source"], "kind": builds.origin_of(store, slug, builds.versions(slug, groups)[0]["path"], index)}
        items.append(item)
    items.sort(key=lambda b: b["updated_at"], reverse=True)
    gallery.ensure_artifacts(settings.GENERATED_DIR / b["latest"]["file"] for b in items[:60]
                             if b["latest"] and (settings.GENERATED_DIR / b["latest"]["file"]).is_file())
    older = [{"id": c["id"], "title": c["title"], "updated_at": c["updated_at"], "running": run_state.is_running(c["id"])}
             for c in builds.older_chats(store)]
    return {"builds": items, "older_chats": older, "pending": sum(1 for b in items if b["pending"])}


class NewBuild(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    source: Optional[dict[str, Any]] = None


@router.post("/api/builds", status_code=201)
async def builds_create(body: NewBuild):
    store = get_store()
    source = body.source if body.source and body.source.get("kind") in builds.SOURCE_KINDS else None
    slug, _record = builds.create(store, body.title or "", source)
    return _detail(store, slug) | {"id": slug}


@router.get("/api/builds/{build_id}")
async def builds_get(build_id: str):
    store = get_store()
    _check(store, build_id)
    return _detail(store, build_id)


class BuildPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/api/builds/{build_id}")
async def builds_rename(build_id: str, body: BuildPatch):
    store = get_store()
    _check(store, build_id)
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(422, "The title cannot be empty")
    builds.update(store, build_id, title=title[:120])
    return _detail(store, build_id)


# --- generators ------------------------------------------------------------------------------

@router.get("/api/generators")
async def generators_list():
    """The generator registry with each one's JSON-schema params, and the colour names the forms offer."""
    if "reply" not in _generators:
        reply = await _worker({"op": "generators"})
        seen, colours = set(), []
        for name, code in reply["colours"].items():       # one primary name per code, like colours.colour_names()
            if code not in seen:
                seen.add(code)
                colours.append({"name": name, "code": code})
        _generators["reply"] = {"generators": reply["generators"], "colours": colours}
    return _generators["reply"]


class GenerateRequest(BaseModel):
    generator: str = Field(max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    params: dict[str, Any] = Field(default_factory=dict)


def _first_error(reply: dict) -> str:
    errors = [d for d in reply.get("diagnostics", []) if d.get("severity") == "error"]
    return "; ".join(str(d.get("message") or d)[:200] for d in errors[:3]) or "The generator's model did not pass its checks"


@router.post("/api/generators/preview")
async def generator_preview(body: GenerateRequest):
    """A quick, unchecked build of (generator, params) for the live preview in the New build form. Cached by the worker."""
    store = get_store()
    folder = store.builds_dir / ".previews"
    folder.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(folder)
    for old in folder.glob("p-*.mpd"):
        if time.time() - old.stat().st_mtime > PREVIEW_KEEP_SECONDS:
            old.unlink(missing_ok=True)
    name = "p-" + hashlib.sha256(f"{body.generator}{sorted(body.params.items())}".encode()).hexdigest()[:20]
    reply = await _worker({"op": "build", "outdir": str(folder), "generator": body.generator, "params": body.params,
                           "name": name, "geometry": False, "glb": False})
    if not reply.get("ok") or "mpd" not in reply.get("paths", {}):
        raise HTTPException(422, _first_error(reply))
    return {"model_url": f"/api/generators/previews/{name}.mpd?v={int(time.time())}", "generator": reply.get("generator")}


@router.get("/api/generators/previews/{name}.mpd")
def generator_preview_file(name: str):
    if not re.fullmatch(r"p-[0-9a-f]{20}", name):
        not_found()
    path = get_store().builds_dir / ".previews" / f"{name}.mpd"
    if not path.is_file():
        not_found()
    return FileResponse(path, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})


@router.post("/api/builds/{build_id}/generate")
async def builds_generate(build_id: str, body: GenerateRequest):
    """Build (generator, params) and save it as the build's next version, remembering the parameters for 'Change settings'."""
    store = get_store()
    _check(store, build_id)
    builds.ensure(store, build_id)
    key = store.build_key(build_id)
    outdir = store.work_dir(key) / "generated"
    outdir.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(outdir)
    reply = await _worker({"op": "build", "outdir": str(outdir), "generator": body.generator, "params": body.params,
                           "name": "model", "geometry": True, "glb": False})
    if not reply.get("ok") or "mpd" not in reply.get("paths", {}):
        raise HTTPException(422, _first_error(reply))
    generated = reply["generator"]
    target = builds.copy_into_generated(store, build_id, Path(reply["paths"]["mpd"]))
    warnings = [d["message"] for d in reply.get("diagnostics", []) if d.get("severity") == "warning" and d.get("message")][:5]
    record = builds.get(store, build_id) or {}
    kind = (record.get("source") or {}).get("kind")
    source = {"kind": kind if kind not in (None, "empty") else "generator",
              "generator": generated["generator"], "params": generated["params"]}
    builds.record_version(store, build_id, record.get("title") or build_id, target, "generator", warnings, source=source)
    gallery.ensure_artifacts([target])
    version = int(re.search(r"-v(\d+)", target.stem).group(1))
    return {"version": version, "file": target.name, "model_url": "/files/generated/" + target.name,
            "generator": generated["generator"], "params": generated["params"], "notes": generated.get("notes", []),
            "cached": reply.get("cached", False)}


# --- import, editor, chat --------------------------------------------------------------------

class ImportRequest(BaseModel):
    filename: str = Field(max_length=200)
    content: str


@router.post("/api/builds/{build_id}/import")
async def builds_import(build_id: str, body: ImportRequest):
    """Save an uploaded .mpd/.ldr as the build's next version."""
    store = get_store()
    _check(store, build_id)
    suffix = Path(body.filename).suffix.lower()
    if suffix not in (".mpd", ".ldr"):
        raise HTTPException(422, "Import an .mpd or .ldr file")
    data = body.content.encode("utf-8")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "This model file is too large to import")
    if not re.search(r"^\s*1\s+-?\d+\s", body.content, re.MULTILINE):
        raise HTTPException(422, "This file has no parts (no LDraw type 1 lines)")
    builds.ensure(store, build_id)
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.GENERATED_DIR / f"{build_id}-v{builds.next_version(build_id)}{suffix}"
    target.write_bytes(data)
    sandbox.give_to_agent(target)
    record = builds.get(store, build_id) or {}
    builds.record_version(store, build_id, record.get("title") or build_id, target, "import", [],
                          source={"kind": "import", "filename": Path(body.filename).name[:120]})
    gallery.ensure_artifacts([target])
    return {"version": int(re.search(r"-v(\d+)", target.stem).group(1)), "file": target.name}


@router.post("/api/builds/{build_id}/edit")
async def builds_edit(build_id: str):
    """Open the build in the 3D editor: from its latest version, or from an empty plan when it has none yet."""
    store = get_store()
    _check(store, build_id)
    builds.ensure(store, build_id)
    files = builds.versions(build_id)
    if files:
        return await edit_model_file(store, files[-1]["path"].name)
    key = store.build_key(build_id)
    view = (await open_session(store, key, name=build_id)) if _session(store, key) is None else _view(store, key)
    return {**view, "build": build_id, "file": None, "editor_url": f"/build/{build_id}/edit"}


class BuildChat(BaseModel):
    llm_model_id: Optional[str] = None
    new: bool = False


@router.post("/api/builds/{build_id}/chat")
async def builds_chat(build_id: str, body: BuildChat):
    """Start a chat attached to the build, or reopen its latest attached chat."""
    store = get_store()
    _check(store, build_id)
    record = builds.ensure(store, build_id)
    if not body.new:
        attached = [c for c in (store.get_chat(i) for i in record["chats"]) if c and c.get("build_id") == build_id]
        if attached:
            return max(attached, key=lambda c: c["updated_at"])
    chat = store.create_chat(llm_model_id=body.llm_model_id, build_id=build_id)
    builds.attach_chat(store, build_id, chat["id"])
    return chat
