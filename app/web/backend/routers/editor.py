"""Editor routes: open, edit, undo, redo and publish a chat's manual plan.

The plan is the source of truth and lives in the chat's work folder:

    output/editor/plan.json     the plan being edited (the same format the agent builds from)
    output/editor/state.json    revision, undo/redo history and the plan's used-piece rows
    output/editor/preview.mpd   the last successfully rebuilt MPD

The backend never imports the toolkit: every change goes through
`ldraw-agent editor ...` (sandboxed, like every other toolkit call), which
applies the task-01 edit operations, enforces the inventory gate in "only"
mode, rebuilds the preview and commits the three files together — nothing is
written when the rebuild fails. Requests are serialised per chat with a lock,
and a stale base revision is a 409 that carries the current revision.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import parts_steering
import sandbox
import toolkit
import tools
from paths import safe_join
from store import get_store
from .common import not_found

router = APIRouter()

TIMEOUT = 600
MAX_EDITS = 100
MAX_NAME = 60
MAX_ROWS = 5000
MAX_PLANS = 20
MAX_SCAN_DEPTH = 3
MAX_PLAN_BYTES = 5_000_000

_locks: dict[str, asyncio.Lock] = {}


def _lock(chat_id: str) -> asyncio.Lock:
    return _locks.setdefault(chat_id, asyncio.Lock())


def _paths(store, chat_id: str):
    folder = store.work_dir(chat_id) / "editor"
    return folder, folder / "plan.json", folder / "state.json", folder / "preview.mpd"


def _session(store, chat_id: str):
    """(plan, state, preview text), or None when the chat has no editor plan."""
    _, plan_path, state_path, mpd_path = _paths(store, chat_id)
    if not (plan_path.is_file() and state_path.is_file() and mpd_path.is_file()):
        return None
    try:
        return (json.loads(plan_path.read_text(encoding="utf-8")),
                json.loads(state_path.read_text(encoding="utf-8")),
                mpd_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _chat(store, chat_id: str) -> dict:
    return store.get_chat(chat_id) or not_found("no such chat")


def _parts_table(used_rows, options) -> dict:
    """Used, owned and available per (part, colour): owned minus what this plan uses.

    The used rows come from the plan (never a stored count), the owned quantities
    from the chat's selected inventories. "Short" lists the pairs the plan uses
    beyond what is owned; the editor refuses those in "only" mode.
    """
    mode, inventories = parts_steering.selection(options)
    owned: dict[tuple[str, int], int] = {}
    for inventory in inventories:
        for part, colour, qty in inventory.rows:
            key = (str(part).casefold(), colour)
            owned[key] = owned.get(key, 0) + qty
    rows, short = [], []
    for part, colour, used in (used_rows or [])[:MAX_ROWS]:
        have = owned.get((str(part).casefold(), colour), 0)
        rows.append({"part": part, "colour": colour, "used": used,
                     "owned": have if mode != "off" else None,
                     "available": max(have - used, 0) if mode != "off" else None})
        if mode != "off" and used > have:
            short.append({"part": part, "colour": colour, "used": used, "owned": have, "missing": used - have})
    return {"mode": mode, "rows": rows, "short": short}


def _view(store, chat_id: str) -> dict:
    session = _session(store, chat_id)
    if session is None:
        not_found("no editor plan for this chat")
    plan, state, preview = session
    return {"plan": plan,
            "revision": state.get("revision", 0),
            "undo_depth": len(state.get("undo") or []),
            "redo_depth": len(state.get("redo") or []),
            "name": state.get("name"),
            "mpd": preview,
            "parts": _parts_table(state.get("used"), _chat(store, chat_id).get("options") or {})}


def _outcome(report: dict, exit_code: int) -> dict:
    """Translate the CLI's report into an HTTP response or exception."""
    if report.get("ok"):
        return report
    if report.get("conflict"):
        raise HTTPException(409, {"detail": report.get("error") or "The plan changed elsewhere; reload and retry",
                                  "revision": report.get("revision")})
    detail: dict[str, Any] = {"detail": report.get("error") or f"editor operation failed (exit code {exit_code})"}
    if report.get("short") is not None:
        detail["short"] = report["short"]
    raise HTTPException(400, detail)


async def _run(store, chat_id: str, arguments: list[str]) -> dict:
    # The report carries the whole plan and MPD, which the sandbox's 20,000-character stdout cap
    # truncates for models of a few hundred parts, so read it from a file the tool writes.
    cwd = toolkit.workspace(store, chat_id)
    report_path = store.work_dir(chat_id) / f".editor-report-{uuid.uuid4().hex}.json"
    result = await sandbox.run(["./ldraw-agent", "editor", "--report", str(report_path), *arguments],
                               cwd=cwd, timeout=TIMEOUT, env_extra=toolkit.environment())
    if result.timed_out or result.exit_code is None:
        report_path.unlink(missing_ok=True)
        raise HTTPException(504, "The editor operation timed out")
    try:
        text = report_path.read_text(encoding="utf-8") if report_path.is_file() else result.stdout
        return json.loads(text)
    except (json.JSONDecodeError, OSError):
        raise HTTPException(500, f"The editor tool did not report JSON (exit code {result.exit_code})") from None
    finally:
        report_path.unlink(missing_ok=True)


class EditorOpen(BaseModel):
    source: Optional[str] = None      # a plan file inside the chat's work folder
    name: Optional[str] = None
    replace: bool = False             # archive the chat's current editor plan and open this one instead


class EditorEdits(BaseModel):
    edits: list[dict[str, Any]] = Field(max_length=MAX_EDITS)
    base_revision: int


class EditorRevision(BaseModel):
    base_revision: Optional[int] = None


class EditorPublish(BaseModel):
    name: Optional[str] = None


def _arguments(store, chat_id: str, action: str, base_revision: Optional[int] = None) -> list[str]:
    _, plan_path, state_path, mpd_path = _paths(store, chat_id)
    arguments = [action, "--plan", str(plan_path), "--state", str(state_path), "--mpd", str(mpd_path)]
    if base_revision is not None:
        arguments += ["--base-revision", str(base_revision)]
    return arguments


@router.get("/api/chats/{chat_id}/editor/plans")
def editor_plans(chat_id: str):
    """The plan files the chat's work folder holds, for the "which model do you want to edit" choice."""
    store = get_store()
    _chat(store, chat_id)
    return {"plans": plan_files(store, chat_id)}


@router.get("/api/chats/{chat_id}/editor")
def editor_get(chat_id: str):
    store = get_store()
    _chat(store, chat_id)
    return _view(store, chat_id)


@router.post("/api/chats/{chat_id}/editor")
async def editor_open(chat_id: str, body: EditorOpen):
    """Start the chat's editor session: from a plan file in its work folder, or a one-brick skeleton."""
    store = get_store()
    return await open_session(store, chat_id, body.source, body.name, body.replace)


async def apply_edits(store, chat_id: str, edits: list, base_revision: int) -> dict:
    """Apply a batch under the chat's lock (shared by the route and the agent's editor_edit tool)."""
    if _session(store, chat_id) is None:
        not_found("no editor plan for this chat")
    async with _lock(chat_id):
        folder, _, _, _ = _paths(store, chat_id)
        folder.mkdir(parents=True, exist_ok=True)
        sandbox.give_to_agent(folder)
        request_path = folder / "request.json"
        request_path.write_text(json.dumps(edits), encoding="utf-8")
        sandbox.give_to_agent(request_path)
        arguments = _arguments(store, chat_id, "apply", base_revision) + ["--edits", str(request_path)]
        mode, inventories = parts_steering.selection(_chat(store, chat_id).get("options") or {})
        if mode in ("prefer", "only") and inventories:
            owned_path = folder / "owned.json"
            owned_path.write_text(json.dumps([list(row) for inventory in inventories for row in inventory.rows]),
                                  encoding="utf-8")
            sandbox.give_to_agent(owned_path)
            arguments += ["--owned", str(owned_path), "--mode", mode]
        report = await _run(store, chat_id, arguments)
        _outcome(report, 0)
        return _view(store, chat_id)


def _archive(folder) -> None:
    """Move the session files aside (editor/archive/<time>/) so a new session can start; nothing is deleted."""
    target = folder / "archive" / time.strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("plan.json", "state.json", "preview.mpd", "request.json", "owned.json"):
        if (folder / name).exists():
            shutil.move(str(folder / name), str(target / name))


def plan_files(store, chat_id: str) -> list[dict]:
    """The plan files in the chat's work folder (what the agent built), newest first.

    A plan is a JSON file with a `sections` list of named sections with steps. The editor's own folder
    and hidden files are skipped; the scan is shallow and capped so a big work folder stays cheap.
    """
    root = store.work_dir(chat_id)
    found = []
    for folder, dirs, files in os.walk(root):
        depth = len(Path(folder).relative_to(root).parts)
        dirs[:] = [d for d in dirs if not d.startswith(".") and not (depth == 0 and d == "editor")] if depth < MAX_SCAN_DEPTH else []
        for file in files:
            path = Path(folder) / file
            if not file.lower().endswith(".json") or file.startswith("."):
                continue
            try:
                if path.stat().st_size > MAX_PLAN_BYTES:
                    continue
                plan = json.loads(path.read_text(encoding="utf-8"))
                sections = plan["sections"]
                entries = sum(len(step) for section in sections for step in section["steps"])
                title = str(sections[0].get("description") or sections[0]["name"])
                found.append({"path": path.relative_to(root).as_posix(), "title": title[:120],
                              "sections": len(sections), "entries": entries, "modified": path.stat().st_mtime})
            except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
                continue
    return sorted(found, key=lambda item: -item["modified"])[:MAX_PLANS]


async def open_session(store, chat_id: str, source: Optional[str] = None, name: Optional[str] = None,
                       replace: bool = False) -> dict:
    """Start the chat's editor session (shared by the route and the agent's editor_open tool)."""
    _chat(store, chat_id)
    _, plan_path, state_path, _ = _paths(store, chat_id)
    if plan_path.exists() or state_path.exists():
        if not replace:
            raise HTTPException(409, "This chat already has an editor plan")
        async with _lock(chat_id):
            _archive(plan_path.parent)
    arguments = _arguments(store, chat_id, "open")
    if source is not None:
        found = safe_join(store.work_dir(chat_id), source.strip())
        if found is None or not found.is_file() or found.suffix.lower() != ".json":
            not_found("source plan not found in the chat's work folder")
        arguments += ["--from", str(found)]
    if name is not None:
        arguments += ["--name", name.strip()[:MAX_NAME]]
    async with _lock(chat_id):
        report = await _run(store, chat_id, arguments)
    _outcome(report, 0)
    return _view(store, chat_id)


@router.post("/api/chats/{chat_id}/editor/edits")
async def editor_edits(chat_id: str, body: EditorEdits):
    """Apply a batch of edit operations; the whole batch is rejected together."""
    store = get_store()
    _chat(store, chat_id)
    return await apply_edits(store, chat_id, body.edits, body.base_revision)


@router.post("/api/chats/{chat_id}/editor/undo")
async def editor_undo(chat_id: str, body: EditorRevision):
    store = get_store()
    _chat(store, chat_id)
    if _session(store, chat_id) is None:
        not_found("no editor plan for this chat")
    async with _lock(chat_id):
        report = await _run(store, chat_id, _arguments(store, chat_id, "undo", body.base_revision))
        _outcome(report, 0)
        return _view(store, chat_id)


@router.post("/api/chats/{chat_id}/editor/redo")
async def editor_redo(chat_id: str, body: EditorRevision):
    store = get_store()
    _chat(store, chat_id)
    if _session(store, chat_id) is None:
        not_found("no editor plan for this chat")
    async with _lock(chat_id):
        report = await _run(store, chat_id, _arguments(store, chat_id, "redo", body.base_revision))
        _outcome(report, 0)
        return _view(store, chat_id)


@router.post("/api/chats/{chat_id}/editor/publish")
async def editor_publish(chat_id: str, body: EditorPublish):
    """Publish the current preview through the same path publish_model uses."""
    store = get_store()
    chat = _chat(store, chat_id)
    session = _session(store, chat_id)
    if session is None:
        not_found("no editor plan for this chat")
    _, state, preview = session
    name = (body.name or state.get("name") or "manual model").strip()[:MAX_NAME] or "manual model"
    target = store.work_dir(chat_id) / f"{tools._slug(name)}.mpd"
    async with _lock(chat_id):
        # Snapshot the committed revision; an edit that lands mid-publication publishes neither more nor less.
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(preview.encode("utf-8"))
        tmp.replace(target)
        sandbox.give_to_agent(target)
    ctx = tools.ToolContext(chat_id, store, lambda *args: None, options=chat.get("options") or {})
    try:
        result = await tools.t_publish_model(ctx, str(target), name)
    except tools.ToolError as exc:
        raise HTTPException(400, str(exc)) from None
    publish = json.JSONDecoder().raw_decode(result.content)[0]
    return {"ok": True, "publish": publish, "parts_check": result.parts_check,
            "models": [ref["id"] for ref in result.models]}
