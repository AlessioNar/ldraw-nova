"""The build workspace API: one draft per build (its editor session), versions, regenerate, describe, parts check and
AI proposals. Contract: docs/plans/build-workspace/CONTRACT.md.

The draft is the build's editor session in space `builds` (routers/editor.py); draft.json beside it remembers which
version it started on and at which revision, so "changes" = revision - base_revision.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import builds
import fast_path
import generator_bridge
import sandbox
import settings
import understand
import workspace
from store import get_store
from .builds_api import GenerateRequest, _check, _first_error, _worker
from .common import not_found
from .editor import (EditorPublish, MAX_PLAN_BYTES, _session, _view, _write_build_meta, _build_meta, editor_publish,
                     open_session, replace_plan)

router = APIRouter()


class DraftOpen(BaseModel):
    version: Optional[int] = None
    force: bool = False


class Describe(BaseModel):
    text: str = Field(max_length=understand.MAX_REQUEST_CHARS * 2)


class Conflict(Exception):
    def __init__(self, changes: int):
        self.changes = changes


def _version_file(slug: str, version: int) -> Path:
    found = {v["version"]: v["path"] for v in builds.versions(slug)}
    if version not in found:
        not_found("no such version")
    return found[version]


def _record(store, slug: str) -> Optional[dict]:
    """draft.json, or a stand-in for a session that predates it (opened by the old editor page): clean, on the
    version its file says."""
    key = store.build_key(slug)
    record = workspace.read_draft(store, key)
    session = _session(store, key)
    if record is not None or session is None:
        return record if session is not None else None
    meta = _build_meta(store, key)
    match = builds.VERSION_RE.match(Path(meta.get("file") or "").stem)
    return {"base_version": int(match["n"]) if match else None, "base_file": meta.get("file"),
            **workspace.base_fields({"revision": session[1].get("revision", 0),
                                     "undo_depth": len(session[1].get("undo") or [])}),
            "updated_at": None}


def _draft(store, slug: str) -> dict:
    key = store.build_key(slug)
    return workspace.draft_summary(_record(store, slug) or {}, _view(store, key))


async def _start(store, slug: str, version: Optional[int]) -> dict:
    """A fresh session on `version` (archiving any session), or on an empty plan when the build has none."""
    key = store.build_key(slug)
    replace = _session(store, key) is not None
    work = store.work_dir(key)
    work.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(work)
    if version is None:
        view = await open_session(store, key, name=slug, replace=replace)
        base_file = None
    else:
        model = _version_file(slug, version)
        if model.stat().st_size > MAX_PLAN_BYTES:
            raise HTTPException(400, "This model is too large to edit")
        copy = work / "source.mpd"
        shutil.copyfile(model, copy)
        sandbox.give_to_agent(copy)
        view = await open_session(store, key, name=slug, replace=replace, mpd=copy)
        _write_build_meta(store, key, model)
        base_file = model.name
    workspace.write_draft(store, key, {"base_version": version, "base_file": base_file, **workspace.base_fields(view)})
    return view


async def _open(store, slug: str, version: Optional[int], force: bool) -> dict:
    """Resume the draft, or start one. Raises Conflict when starting elsewhere would drop changes."""
    builds.ensure(store, slug)
    key = store.build_key(slug)
    files = builds.versions(slug)
    latest = files[-1]["version"] if files else None
    if version is not None:
        _version_file(slug, version)
    record = _record(store, slug)
    if record is not None:
        draft = workspace.draft_summary(record, _view(store, key))
        if version is None or version == draft["base_version"]:
            if workspace.read_draft(store, key) is None:       # adopt a session the old editor page started
                workspace.write_draft(store, key, record)
            return _view(store, key)
        if draft["changes"] and not force:
            raise Conflict(draft["changes"])
    return await _start(store, slug, version if version is not None else latest)


def _conflict(exc: Conflict) -> JSONResponse:
    return JSONResponse({"detail": f"The draft has {exc.changes} change{'s' if exc.changes != 1 else ''}; "
                                   "starting from another version drops them", "changes": exc.changes}, status_code=409)


async def _ensure_draft(store, slug: str) -> None:
    _check(store, slug)
    if _session(store, store.build_key(slug)) is None:
        await _open(store, slug, None, False)


# --- the workspace ---------------------------------------------------------------------------------------------

def _generator(record: dict) -> Optional[dict]:
    source = record.get("source") or {}
    return {"id": source["generator"], "params": source.get("params") or {}} if source.get("generator") else None


@router.get("/api/builds/{build_id}/workspace")
async def workspace_get(build_id: str):
    store = get_store()
    record = _check(store, build_id)
    key = store.build_key(build_id)
    index = builds.Index(store)
    owned, signature = workspace.owned_parts()
    rows = []
    for row in builds.version_rows(store, build_id, index=index):
        path = settings.GENERATED_DIR / row["file"]
        check = workspace.check(workspace.file_usage(path), owned, signature)
        rows.append({"version": row["version"], "file": row["file"], "origin": row["origin"], "parts": check["needed"],
                     "changes": row["changes"], "title": row["title"], "created_at": row["created_at"],
                     "image_url": row["image_url"],
                     "parts_check": {"owned": check["owned"], "needed": check["needed"],
                                     "missing": check["needed"] - check["owned"]}})
    draft = None
    stored = _record(store, build_id)
    if stored is not None:
        draft = workspace.draft_summary(stored, _view(store, key))
    attached = [c for c in index.chats_of(build_id) if c.get("build_id") == build_id]
    summary = builds.summary(store, build_id, record, index, builds.group_versions())
    return {"build": summary, "versions": rows, "draft": draft, "generator": _generator(record),
            "proposals": workspace.list_proposals(store, build_id), "chat_id": attached[0]["id"] if attached else None}


# --- the draft ------------------------------------------------------------------------------------------------------

@router.post("/api/builds/{build_id}/draft/open")
async def draft_open(build_id: str, body: Optional[DraftOpen] = None):
    body = body or DraftOpen()
    store = get_store()
    _check(store, build_id)
    try:
        view = await _open(store, build_id, body.version, body.force)
    except Conflict as exc:
        return _conflict(exc)
    return {**view, "build": build_id, "draft": _draft(store, build_id)}


@router.post("/api/builds/{build_id}/draft/save")
async def draft_save(build_id: str):
    """The draft becomes the build's next version and stays open on it with no changes."""
    store = get_store()
    _check(store, build_id)
    key = store.build_key(build_id)
    if _session(store, key) is None:
        not_found("there is no draft to save")
    if not _draft(store, build_id)["changes"]:
        return JSONResponse({"detail": "The draft has no changes to save", "changes": 0}, status_code=409)
    saved = await editor_publish("builds", build_id, EditorPublish())
    view = _view(store, key)
    record = _record(store, build_id) or {}
    workspace.write_draft(store, key, {**record, "base_version": saved["version"], "base_file": saved["file"],
                                       **workspace.base_fields(view)})
    return {"version": saved["version"], "file": saved["file"], "message": saved["message"],
            "draft": _draft(store, build_id)}


@router.post("/api/builds/{build_id}/draft/discard")
async def draft_discard(build_id: str):
    """Back to the version the draft started on, with no changes."""
    store = get_store()
    _check(store, build_id)
    record = _record(store, build_id)
    if record is None:
        not_found("there is no draft to discard")
    version = record.get("base_version")
    if version is not None and version not in {v["version"] for v in builds.versions(build_id)}:
        version = None
    view = await _start(store, build_id, version)
    return {**view, "build": build_id, "draft": _draft(store, build_id)}


async def _regenerate(store, slug: str, generator: str, params: dict) -> dict:
    """The generator's model replaces the draft's plan as ONE undoable step."""
    await _ensure_draft(store, slug)
    key = store.build_key(slug)
    replaced = _draft(store, slug)["changes"] > 0
    outdir = store.work_dir(key) / "generated"
    outdir.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(outdir)
    reply = await _worker({"op": "build", "outdir": str(outdir), "generator": generator, "params": params,
                           "name": "model", "geometry": False, "glb": False})
    if not reply.get("ok") or "mpd" not in reply.get("paths", {}):
        raise HTTPException(422, _first_error(reply))
    built = reply["generator"]
    await replace_plan(store, key, Path(reply["paths"]["mpd"]))
    record = builds.get(store, slug) or {}
    kind = (record.get("source") or {}).get("kind")
    builds.update(store, slug, source={"kind": kind if kind not in (None, "empty") else "generator",
                                       "generator": built["generator"], "params": built["params"]})
    return {"replaced_manual_edits": replaced, "generator": {"id": built["generator"], "params": built["params"]},
            "notes": built.get("notes", []), "draft": _draft(store, slug)}


@router.post("/api/builds/{build_id}/draft/regenerate")
async def draft_regenerate(build_id: str, body: GenerateRequest):
    store = get_store()
    _check(store, build_id)
    return await _regenerate(store, build_id, body.generator, body.params)


@router.post("/api/builds/{build_id}/draft/describe")
async def draft_describe(build_id: str, body: Describe):
    """The fast path on the build's generator settings. A generator or edit answer is regenerated into the draft;
    needs_ai and clarify are returned as they are, with nothing spent and the draft untouched."""
    if not body.text.strip():
        raise HTTPException(422, "text is empty")
    store = get_store()
    record = _check(store, build_id)
    registry = await generator_bridge.ensure()
    current = _generator(record)
    if current and registry and not any(e["id"] == current["id"] for e in registry["entries"]):
        current = None
    decision = await fast_path.describe(body.text, current={"generator": current["id"], "params": current["params"]}
                                        if current else None, build_id=build_id)
    if decision.get("route") in ("generator", "edit"):
        done = await _regenerate(store, build_id, decision["generator"], decision.get("params") or {})
        return {**decision, "replaced_manual_edits": done["replaced_manual_edits"], "draft": done["draft"]}
    draft = _draft(store, build_id) if _record(store, build_id) is not None else None
    return {**decision, "draft": draft}


@router.get("/api/builds/{build_id}/draft/parts-check")
async def draft_parts_check(build_id: str):
    """The draft against all of the person's parts lists."""
    store = get_store()
    _check(store, build_id)
    session = _session(store, store.build_key(build_id))
    if session is None:
        not_found("there is no draft")
    return workspace.check(workspace.rows_usage(session[1].get("used")))


# --- proposals --------------------------------------------------------------------------------------------------------

def _proposal(store, slug: str, pid: str) -> Path:
    _check(store, slug)
    return workspace.proposal_file(store, slug, pid) or not_found("no such proposal")


@router.get("/api/builds/{build_id}/proposals")
async def proposals_list(build_id: str):
    store = get_store()
    _check(store, build_id)
    return {"proposals": workspace.list_proposals(store, build_id)}


@router.post("/api/builds/{build_id}/proposals/{pid}/accept")
async def proposal_accept(build_id: str, pid: str):
    """The proposal replaces the draft's plan as ONE undoable step (the draft is opened first when there is none)."""
    store = get_store()
    path = _proposal(store, build_id, pid)
    await _ensure_draft(store, build_id)
    key = store.build_key(build_id)
    replaced = _draft(store, build_id)["changes"] > 0
    await replace_plan(store, key, path)
    workspace.delete_proposal(store, build_id, pid)
    return {"accepted": pid, "replaced_manual_edits": replaced, "draft": _draft(store, build_id),
            "proposals": workspace.list_proposals(store, build_id)}


@router.post("/api/builds/{build_id}/proposals/{pid}/discard")
async def proposal_discard(build_id: str, pid: str):
    store = get_store()
    _proposal(store, build_id, pid)
    workspace.delete_proposal(store, build_id, pid)
    return {"discarded": pid, "proposals": workspace.list_proposals(store, build_id)}


@router.get("/api/builds/{build_id}/proposals/{pid}.mpd")
async def proposal_mpd(build_id: str, pid: str):
    """The proposed model, for a read-only preview in the canvas (view-version)."""
    return FileResponse(_proposal(get_store(), build_id, pid), media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})
