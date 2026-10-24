"""Editor routes: open, edit, undo, redo and publish a chat's manual plan.

The plan is the source of truth and lives in the chat's work folder:

    output/editor/plan.json     the plan being edited (the same format the agent builds from)
    output/editor/state.json    revision, undo/redo history and the plan's used-piece rows
    output/editor/preview.mpd   the last successfully rebuilt MPD

The backend never imports the toolkit: every change goes through the toolkit's
`editor` action, run in a resident, warm toolkit process (build_worker's editor
worker; one sandboxed `ldraw-agent editor ...` process per call is the fallback
and what agents use). The action applies the task-01 edit operations, enforces the inventory gate in "only"
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

import build_worker
import builds
import parts_steering
import sandbox
import settings
import toolkit
import tools
import workspace
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
# Editor operations run in a resident toolkit process (see build_worker.get_editor_worker): the CLI pays seconds of
# start-up and library loading on every call. LDRAW_NOVA_EDITOR_WORKER=0 goes back to one CLI process per call.
EDITOR_WORKER = os.environ.get("LDRAW_NOVA_EDITOR_WORKER", "1") != "0"
CLI_OPTIONS = {"--plan": "plan", "--state": "state", "--mpd": "mpd", "--base-revision": "base_revision",
               "--edits": "edits", "--owned": "owned", "--mode": "mode", "--from": "source",
               "--from-mpd": "from_mpd", "--name": "name"}

EMPTY_PLAN = {"version": 1, "author": "LDraw Nova editor", "sections": [
    {"name": "model.ldr", "description": "Manual editor model", "steps": [[]]}]}

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


def _key(space: str, chat_id: str) -> str:
    """The workspace key for a route: a chat's id, or a build's b-<slug> (the same editor serves both)."""
    if space == "chats":
        if get_store().is_build(chat_id):
            not_found("no such chat")
        return chat_id
    if space == "builds":
        return get_store().build_key(chat_id)
    not_found("no such workspace")


def _chat(store, chat_id: str) -> dict:
    """The workspace's record: the chat, or for a build a stand-in with no options (no inventory steering)."""
    if store.is_build(chat_id):
        if not store.valid_id(chat_id) or not store.chat_dir(chat_id).is_dir():
            not_found("no such build")
        return {"id": chat_id, "options": {}}
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
            "warnings": state.get("warnings") or [],
            "mpd": preview,
            "parts": _parts_table(state.get("used"), _chat(store, chat_id).get("options") or {})}


def _delta_view(store, chat_id: str, report: dict) -> Optional[dict]:
    """The small answer to an edit, undo or redo: the splices of the plan's steps that changed (see the toolkit's
    `plan_delta`), the new revision and depths and the parts table. None when the report has no delta (the plan's
    shape changed, or an older toolkit): the caller sends the whole view. The preview MPD is not part of it; it is on
    disk, and the canvas draws from the changed entries."""
    if report.get("delta") is None or "revision" not in report:
        return None
    return {"delta": True, "revision": report["revision"], "undo_depth": report.get("undo_depth", 0),
            "redo_depth": report.get("redo_depth", 0), "splices": report["delta"],
            "parts": _parts_table(report.get("used"), _chat(store, chat_id).get("options") or {})}


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


def _request(arguments: list[str]) -> dict:
    """The CLI arguments of one editor action as the worker's request object."""
    request: dict[str, Any] = {"op": "editor", "action": arguments[0]}
    for flag, value in zip(arguments[1::2], arguments[2::2]):
        request[CLI_OPTIONS[flag]] = int(value) if flag == "--base-revision" else value
    return request


async def _run(store, chat_id: str, arguments: list[str]) -> dict:
    if EDITOR_WORKER:
        work = store.work_dir(chat_id)           # the CLI path gets this from toolkit.workspace
        work.mkdir(parents=True, exist_ok=True)
        sandbox.give_to_agent(work)
        try:
            reply = await build_worker.get_editor_worker().request(_request(arguments), timeout=TIMEOUT)
        except build_worker.WorkerTimeout:
            raise HTTPException(504, "The editor operation timed out") from None
        except build_worker.WorkerError:
            reply = None                    # busy or crashed: this call goes through the CLI, the next restarts the worker
        if reply is not None and "exit_code" in reply:      # an older toolkit does not know the op: use the CLI
            return reply
    return await _run_cli(store, chat_id, arguments)


async def _run_cli(store, chat_id: str, arguments: list[str]) -> dict:
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
    delta: bool = False               # answer with the difference (see _delta_view) instead of the whole plan and preview


class EditorRevision(BaseModel):
    base_revision: Optional[int] = None
    delta: bool = False


class EditorPublish(BaseModel):
    name: Optional[str] = None


def _arguments(store, chat_id: str, action: str, base_revision: Optional[int] = None) -> list[str]:
    _, plan_path, state_path, mpd_path = _paths(store, chat_id)
    arguments = [action, "--plan", str(plan_path), "--state", str(state_path), "--mpd", str(mpd_path)]
    if base_revision is not None:
        arguments += ["--base-revision", str(base_revision)]
    return arguments


@router.get("/api/{space}/{chat_id}/editor/plans")
def editor_plans(space: str, chat_id: str):
    """The plan files the chat's work folder holds, for the "which model do you want to edit" choice."""
    store = get_store()
    chat_id = _key(space, chat_id)
    _chat(store, chat_id)
    return {"plans": plan_files(store, chat_id)}


_warmed: dict[str, int] = {}      # workspace key -> id of the editor worker that was told to warm up for it


def warm(store, chat_id: str) -> None:
    """Start the editor worker (library load) and have it load the geometry of the plan's part types, in the
    background, once per worker: the first edit and snap of a session then do not pay for them. Needs a running loop."""
    if not EDITOR_WORKER:
        return
    try:
        worker = build_worker.get_editor_worker()
        if _warmed.get(chat_id) == id(worker):
            return
        _warmed[chat_id] = id(worker)
        request = {"op": "editor", "action": "warm", "plan": str(_paths(store, chat_id)[1])}

        async def go():
            try:
                await worker.request(request, timeout=TIMEOUT)
            except Exception:                       # busy, crashed: the next edit starts it anyway
                _warmed.pop(chat_id, None)
        asyncio.get_running_loop().create_task(go())
    except Exception:
        _warmed.pop(chat_id, None)


@router.get("/api/{space}/{chat_id}/editor")
async def editor_get(space: str, chat_id: str):
    store = get_store()
    chat_id = _key(space, chat_id)
    _chat(store, chat_id)
    view = await asyncio.to_thread(_view, store, chat_id)
    warm(store, chat_id)
    return view


@router.post("/api/{space}/{chat_id}/editor")
async def editor_open(space: str, chat_id: str, body: EditorOpen):
    """Start the chat's editor session: from a plan file in its work folder, or a one-brick skeleton."""
    store = get_store()
    chat_id = _key(space, chat_id)
    return await open_session(store, chat_id, body.source, body.name, body.replace)


async def apply_edits(store, chat_id: str, edits: list, base_revision: int, delta: bool = False) -> dict:
    """Apply a batch under the chat's lock (shared by the route and the agent's editor_edit tool).

    `delta` answers with the difference (`_delta_view`) when the toolkit reports one; the agent gets the whole view."""
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
        return await _answer(store, chat_id, report, "edit", delta)


def _touch(store, chat_id: str, kind: str = "edit", view: Optional[dict] = None) -> None:
    if store.is_build(chat_id):
        workspace.touch_draft(store, chat_id, kind, view if view is not None else _view(store, chat_id))


async def _answer(store, chat_id: str, report: dict, kind: str, delta: bool) -> dict:
    """The response to a committed edit: the difference when asked for and available, else the whole view."""
    small = _delta_view(store, chat_id, report) if delta else None
    if small is not None:
        _touch(store, chat_id, kind, small)         # only the undo depth is read: no need to read the files again
        return small
    view = await asyncio.to_thread(_view, store, chat_id)
    _touch(store, chat_id, kind, view)
    return view


async def replace_plan(store, chat_id: str, mpd: Path, base_revision: Optional[int] = None) -> dict:
    """Swap the whole plan for the model in `mpd` as ONE undoable step (regenerate, accepting a proposal)."""
    if _session(store, chat_id) is None:
        not_found("no editor plan for this chat")
    async with _lock(chat_id):
        work = store.work_dir(chat_id)
        work.mkdir(parents=True, exist_ok=True)
        sandbox.give_to_agent(work)
        copy = work / "replace.mpd"
        shutil.copyfile(mpd, copy)
        sandbox.give_to_agent(copy)
        report = await _run(store, chat_id, _arguments(store, chat_id, "replace", base_revision) + ["--from-mpd", str(copy)])
        _outcome(report, 0)
        _touch(store, chat_id)
        return _view(store, chat_id)


def _archive(folder) -> None:
    """Move the session files aside (editor/archive/<time>/) so a new session can start; nothing is deleted."""
    target = folder / "archive" / time.strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("plan.json", "state.json", "preview.mpd", "request.json", "owned.json", "agent-seen.json"):
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
                       replace: bool = False, mpd: Optional[Path] = None, empty: bool = False) -> dict:
    """Start the chat's editor session (shared by the route and the agent's editor_open tool).

    `empty` opens a plan with no bricks (a new build); otherwise the toolkit's one-brick skeleton."""
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
    if empty:
        blank = store.work_dir(chat_id) / "empty-plan.json"
        blank.parent.mkdir(parents=True, exist_ok=True)
        blank.write_text(json.dumps(EMPTY_PLAN), encoding="utf-8")
        sandbox.give_to_agent(blank)
        arguments += ["--from", str(blank)]
    if mpd is not None:      # a model file (inside the work folder) imported as the plan
        arguments += ["--from-mpd", str(mpd)]
    if name is not None:
        arguments += ["--name", name.strip()[:MAX_NAME]]
    async with _lock(chat_id):
        report = await _run(store, chat_id, arguments)
    _outcome(report, 0)
    return _view(store, chat_id)


@router.post("/api/{space}/{chat_id}/editor/edits")
async def editor_edits(space: str, chat_id: str, body: EditorEdits):
    """Apply a batch of edit operations; the whole batch is rejected together."""
    store = get_store()
    chat_id = _key(space, chat_id)
    _chat(store, chat_id)
    return await apply_edits(store, chat_id, body.edits, body.base_revision, body.delta)


@router.post("/api/{space}/{chat_id}/editor/undo")
async def editor_undo(space: str, chat_id: str, body: EditorRevision):
    store = get_store()
    chat_id = _key(space, chat_id)
    _chat(store, chat_id)
    if _session(store, chat_id) is None:
        not_found("no editor plan for this chat")
    async with _lock(chat_id):
        report = await _run(store, chat_id, _arguments(store, chat_id, "undo", body.base_revision))
        _outcome(report, 0)
        return await _answer(store, chat_id, report, "undo", body.delta)


@router.post("/api/{space}/{chat_id}/editor/redo")
async def editor_redo(space: str, chat_id: str, body: EditorRevision):
    store = get_store()
    chat_id = _key(space, chat_id)
    _chat(store, chat_id)
    if _session(store, chat_id) is None:
        not_found("no editor plan for this chat")
    async with _lock(chat_id):
        report = await _run(store, chat_id, _arguments(store, chat_id, "redo", body.base_revision))
        _outcome(report, 0)
        return await _answer(store, chat_id, report, "redo", body.delta)


@router.post("/api/{space}/{chat_id}/editor/publish")
async def editor_publish(space: str, chat_id: str, body: EditorPublish):
    """Publish the current preview through the same path publish_model uses."""
    store = get_store()
    chat_id = _key(space, chat_id)
    chat = _chat(store, chat_id)
    session = _session(store, chat_id)
    if session is None:
        not_found("no editor plan for this chat")
    _, state, preview = session
    if not sum(row[2] for row in state.get("used") or []):
        raise HTTPException(400, "Add at least one brick before saving a version")
    build = store.is_build(chat_id)
    # A build always saves under its own name: the next version of the same build, whatever the body says.
    name = ((state.get("name") if build else body.name or state.get("name")) or "manual model").strip()[:MAX_NAME] or "manual model"
    target = store.work_dir(chat_id) / f"{tools._slug(name)}.mpd"
    async with _lock(chat_id):
        # Snapshot the committed revision; an edit that lands mid-publication publishes neither more nor less.
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(preview.encode("utf-8"))
        tmp.replace(target)
        sandbox.give_to_agent(target)
    ctx = tools.ToolContext(chat_id, store, lambda *args: None, options=chat.get("options") or {}, origin="editor")
    try:
        result = await tools.t_publish_model(ctx, str(target), name)
    except tools.ToolError as exc:
        raise HTTPException(400, str(exc)) from None
    publish = json.JSONDecoder().raw_decode(result.content)[0]
    ref = result.models[0]
    version = ref.get("version")
    pieces = sum(row[2] for row in state.get("used") or [])
    check = result.parts_check
    if build:
        saved = store.resolve(chat_id, ref["model"])
        meta = _build_meta(store, chat_id)
        _write_build_meta(store, chat_id, saved)      # the session now continues from the saved version
        return {"ok": True, "publish": publish, "parts_check": None, "models": [ref["id"]], "model_id": ref["id"],
                "version": version, "name": name, "pieces": pieces, "file": saved.name,
                "previous": meta.get("file"), "builds_url": "/models",
                "message": f"Saved as v{version} in My Builds"}
    note = f" Parts check: {check['summary']}" if check else ""
    # Record the person's publish in the chat: it is also what the agent reads on its next turn.
    store.add_message(chat_id, {
        "role": "user", "_editor_publish": True, "_models": [ref["id"]],
        "_label": f"You edited the plan in the 3D editor and published it as v{version}",
        "content": f"I edited the plan in the 3D editor (revision {state.get('revision', 0)}, {pieces} pieces) and "
                   f"published it as v{version}, {name}. The plan is output/editor/plan.json and the published "
                   f"file output/{target.name}.{note}",
        **({"_parts_check": check} if check else {})})
    return {"ok": True, "publish": publish, "parts_check": check, "models": [ref["id"]], "model_id": ref["id"],
            "version": version, "name": name, "pieces": pieces, "chat_url": f"/chat/{chat_id}#model-{ref['id']}"}


# --- builds: the editor on a model in My Builds, with no chat -----------------------------------------------

def build_id_of(filename: str) -> str:
    """The build a model file belongs to: its name without extension and `-vN` version, as the publish slug."""
    return builds.slug_of_file(filename)


def _build_meta(store, key: str) -> dict:
    try:
        return json.loads((store.chat_dir(key) / "edit.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_build_meta(store, key: str, model: Path) -> None:
    """Remember which model file the session continues from (and its mtime, to notice a changed file)."""
    (store.chat_dir(key) / "edit.json").write_text(
        json.dumps({"file": model.name, "mtime_ns": model.stat().st_mtime_ns}), encoding="utf-8")


async def edit_model_file(store, filename: str) -> dict:
    """Open a My Builds model in the 3D editor: resume this build's session when it continues from this exact
    file, else import the file as a fresh plan (the old session is archived). No chat is created."""
    root = settings.GENERATED_DIR.resolve()
    model = root / filename
    if (Path(filename).name != filename or filename.startswith(".") or model.suffix.lower() not in (".mpd", ".ldr")
            or model.is_symlink() or not model.is_file()):
        not_found("Model not found")
    if model.stat().st_size > MAX_PLAN_BYTES:
        raise HTTPException(400, "This model is too large to edit")
    build = build_id_of(filename)
    builds.ensure(store, build)
    key = store.build_key(build)
    work = store.work_dir(key)
    work.mkdir(parents=True, exist_ok=True)
    sandbox.give_to_agent(work)
    meta = _build_meta(store, key)
    view = None
    if _session(store, key) is not None and meta.get("file") == filename and meta.get("mtime_ns") == model.stat().st_mtime_ns:
        view = _view(store, key)
    if view is None:
        copy = work / "source.mpd"
        shutil.copyfile(model, copy)
        sandbox.give_to_agent(copy)
        view = await open_session(store, key, name=build, replace=True, mpd=copy)
        _write_build_meta(store, key, model)
    warm(store, key)
    return {**view, "build": build, "file": filename, "editor_url": f"/build/{build}/edit"}


@router.post("/api/models/{filename}/edit")
async def models_edit(filename: str):
    return await edit_model_file(get_store(), filename)
