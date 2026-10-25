"""The build workspace API: one draft per build (its editor session), versions, regenerate, describe, parts check and
AI proposals. Contract: docs/plans/build-workspace/CONTRACT.md.

The draft is the build's editor session in space `builds` (routers/editor.py); draft.json beside it remembers which
version it started on and at which revision, so "changes" = revision - base_revision.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import builds
import chat_routing
import fast_path
import generator_bridge
import sandbox
import settings
import tools
import understand
import workspace
from store import get_store
from .builds_api import GenerateRequest, _check, _first_error, _worker, attached_chat
from .common import not_found
from .editor import (EditorPublish, EditorRevision, MAX_PLAN_BYTES, _session, _view, _write_build_meta, _build_meta,
                     apply_role_edit, editor_publish, editor_undo, open_session, replace_plan)

router = APIRouter()


class DraftOpen(BaseModel):
    version: Optional[int] = None
    force: bool = False


class Describe(BaseModel):
    text: str = Field(max_length=understand.MAX_REQUEST_CHARS * 2)
    previous_text: Optional[str] = Field(default=None, max_length=understand.MAX_REQUEST_CHARS * 2)   # "again", "a bit less"
    persist: bool = True            # write the turn into the build's chat (one saved history per build)
    quick: bool = False             # the person chose "Quick change instead" for this message while the AI is engaged
    selection: list[str] = Field(default_factory=list, max_length=500)   # ids of the pieces selected on the canvas ("paint this red")


class Note(BaseModel):
    text: str = Field(max_length=500)


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
        view = await open_session(store, key, name=slug, replace=replace, empty=True)
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
    source = (builds.get(store, slug) or {}).get("source") or {}
    if version is not None and source.get("generator") and not source.get("diverged"):
        builds.update(store, slug, source={**source, "plan_sha": _plan_sha(store, key)})    # the saved version IS what it made
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
    return {"id": source["generator"], "params": source.get("params") or {}, "plan_sha": source.get("plan_sha")} if source.get("generator") else None


def _source_now(store, slug: str, record: dict) -> Optional[dict]:
    """The generator settings that belong to the draft's current step. Each regenerate remembers its settings under the
    undo depth it reached (draft.json `sources`), so Undo and Redo walk back and forth through them and a later "make the
    roof blue" is judged against what is on the canvas, not against the last regenerate."""
    found = _generator(record)
    stored = _record(store, slug)
    sources = (stored or {}).get("sources") or {}
    if not sources:
        return found
    depth = _draft(store, slug)["undo_depth"]
    below = [int(k) for k in sources if int(k) <= depth]
    return sources[str(max(below))] if below else found


def _plan_sha(store, key: str) -> Optional[str]:
    """A short hash of the draft's plan: what "the draft still equals what the generator last made" is judged by."""
    session = _session(store, key)
    return hashlib.sha256(json.dumps(session[0], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16] if session else None


def _live_source(store, slug: str, record: dict) -> tuple[Optional[dict], bool]:
    """(the generator settings the draft still matches, or None; whether the build HAS a source the draft moved away from).

    A draft diverges from its source by manual edits, an accepted AI proposal, a saved version of either, or an import.
    A text edit on a diverged draft never regenerates over it (it edits the model in place by role)."""
    entry = _source_now(store, slug, record)
    if not entry or not entry.get("id"):
        return None, False
    key = store.build_key(slug)
    sha = entry.get("plan_sha")
    if sha is not None:
        diverged = sha != _plan_sha(store, key)
    else:                                                 # generated by a version build: only the draft's changes tell
        diverged = bool((record.get("source") or {}).get("diverged")) or (
            _record(store, slug) is not None and _draft(store, slug)["changes"] > 0)
    return (None, True) if diverged else (entry, False)


def _chat_messages(store, build_id: str) -> list[dict]:
    """The build's saved chat, without creating one."""
    attached = [c for c in builds.Index(store).chats_of(build_id) if c.get("build_id") == build_id]
    return store.messages(attached[0]["id"]) if attached else []


def _turn_line(text: str, meta: dict) -> str:
    what = "; ".join(f"{c.get('label')}: {c.get('from')} -> {c.get('to')}" for c in meta.get("changes") or []) or meta.get("question") or meta.get("route") or ""
    return f"{text[:80]} -> {what[:80]}"


def build_context(store, slug: str, record: dict, selection: Optional[list] = None) -> dict:
    """What the decision engine needs to know about the build: live generator source, divergence, parts and roles of the
    draft, the selection, the last change and the last turns. Cheap (reads files only); the hint and describe share it."""
    current, diverged = _live_source(store, slug, record)
    session = _session(store, store.build_key(slug))
    parts, roles = 0, {}
    if session:
        parts = sum(row[2] for row in session[1].get("used") or [])
        names = {s["name"].casefold() for s in session[0]["sections"]}
        roles = dict(collections.Counter(e.get("role", "untagged") for s in session[0]["sections"] for step in s["steps"]
                                         for e in step if e["ref"].casefold() not in names).most_common(8))
    has_model = parts > 0 or (session is None and bool(builds.versions(slug)))
    messages = _chat_messages(store, slug)
    turns, last = [], None
    users = [i for i, m in enumerate(messages) if m.get("role") == "user" and not m.get("_hidden") and isinstance(m.get("content"), str)]
    for i in users[-3:]:
        reply = next((m for m in messages[i + 1:] if m.get("role") == "assistant"), None)
        turns.append(_turn_line(messages[i]["content"], (reply or {}).get("_fast_path") or {}))
    if messages and messages[-1].get("role") == "assistant":
        meta = messages[-1].get("_fast_path") or {}
        if meta.get("route") in ("edit", "generator") and meta.get("changes"):
            last = {"changes": meta["changes"]}
    previous = next((messages[i]["content"] for i in reversed(users)), None)
    return {"has_model": has_model, "parts": parts, "roles": roles, "diverged": diverged, "selection": len(selection or []),
            "selection_ids": list(selection or []), "last": last, "turns": turns, "previous": previous,
            "current": ({"generator": current["id"], "params": current["params"]} if current else None)}


def hint_context(build_id: str) -> tuple[Optional[dict], dict]:
    """(current, context) for GET /api/describe/estimate: the same view of the build the decision will have."""
    store = get_store()
    record = _check(store, build_id)
    context = build_context(store, build_id, record)
    return context.pop("current"), context


EXPLICIT_REGENERATE = re.compile(r"^(?:please )?(?:regenerate|re-generate|rebuild|start over|start again|reset)(?: (?:it|the model|the build|from the settings|from scratch))?[.!]?$", re.I)
CONFIRM = {"yes, change them", "yes, start over", "just pick for me", "yes", "yes please", "ok", "do it", "go ahead"}


def _pending(messages: list[dict]) -> Optional[dict]:
    """What the last quick-change answer was waiting for the person to confirm, if anything."""
    last = messages[-1] if messages else {}
    return (last.get("_fast_path") or {}).get("pending") if last.get("role") == "assistant" else None


async def _role_edit(store, build_id: str, decision: dict, selection: list, force: bool) -> dict:
    """Role edits on the CURRENT draft plan: one undoable editor step, answered with the delta."""
    await _ensure_draft(store, build_id)
    key = store.build_key(build_id)
    if decision.get("history"):
        view = await editor_undo("builds", build_id, EditorRevision(delta=True))
        return {**decision, "role_edits": None, "draft": _draft(store, build_id), "delta": view if view.get("delta") else None}
    spec = {"edits": decision["role_edits"], "selection": selection, "force": force}
    done = await apply_role_edit(store, key, spec)
    if not done.get("applied"):
        clarify = done.get("clarify") or {"question": "That did not change anything. What would you like to change?", "choices": []}
        pending = {"kind": "role_edit", "edits": decision["role_edits"], "selection": selection} if clarify.get("role") else None
        return {"route": "clarify", "question": clarify["question"], "choices": clarify.get("choices") or [],
                **({"pending": pending, "highlight": clarify.get("ids")} if pending else {}), "final": True,
                "notes": done.get("notes") or [], "draft": _draft(store, build_id)}
    answer = {**decision, "changes": done.get("changes") or decision.get("changes"), "notes": [*decision.get("notes", []), *(done.get("notes") or [])],
              "draft": _draft(store, build_id)}
    if done.get("view"):
        answer["delta"] = done["view"]
    return answer


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
                     "image_url": row["image_url"], "check": tools.read_check(path),
                     "parts_check": {"owned": check["owned"], "needed": check["needed"],
                                     "missing": check["needed"] - check["owned"]}})
    draft = None
    stored = _record(store, build_id)
    if stored is not None:
        draft = workspace.draft_summary(stored, _view(store, key))
    attached = [c for c in index.chats_of(build_id) if c.get("build_id") == build_id]
    mode = _chat_state(store, build_id)
    summary = builds.summary(store, build_id, record, index, builds.group_versions())
    return {"build": summary, "versions": rows, "draft": draft, "generator": _generator(record),
            "proposals": workspace.list_proposals(store, build_id), "chat_id": attached[0]["id"] if attached else None,
            "chat_mode": mode["mode"], "agent_chat": mode["has_agent"]}


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
    _, diverged = _live_source(store, build_id, builds.get(store, build_id) or {})
    saved = await editor_publish("builds", build_id, EditorPublish())
    source = (builds.get(store, build_id) or {}).get("source") or {}
    if diverged and source.get("generator"):          # the saved version carries edits the settings no longer describe
        builds.update(store, build_id, source={**source, "diverged": True})
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
    before, previous = _draft(store, slug)["undo_depth"], _source_now(store, slug, builds.get(store, slug) or {})
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
    sha = _plan_sha(store, key)
    builds.update(store, slug, source={"kind": kind if kind not in (None, "empty") else "generator",
                                       "generator": built["generator"], "params": built["params"], "plan_sha": sha})
    stored = workspace.read_draft(store, key)
    if stored is not None:                       # remember the settings of this step (a new step drops the redo ones)
        sources = {k: v for k, v in (stored.get("sources") or {}).items() if int(k) <= before}
        if previous:
            sources.setdefault(str(before), previous)
        sources[str(_draft(store, slug)["undo_depth"])] = {"id": built["generator"], "params": built["params"], "plan_sha": sha}
        workspace.write_draft(store, key, {**stored, "sources": sources})
    return {"replaced_manual_edits": replaced, "generator": {"id": built["generator"], "params": built["params"]},
            "notes": built.get("notes", []), "draft": _draft(store, slug)}


@router.post("/api/builds/{build_id}/draft/regenerate")
async def draft_regenerate(build_id: str, body: GenerateRequest):
    store = get_store()
    _check(store, build_id)
    return await _regenerate(store, build_id, body.generator, body.params)


STARTERS = ["a small house", "a small red car", "a tree"]
TWEAKS = ["make the roof red", "make the walls blue"]            # rules-understood, so the chip works without a model


def _chat_state(store, build_id: str) -> dict:
    """Who answers the next message (chat_routing.state) from the build's saved chat; nothing is created."""
    index = builds.Index(store)
    attached = [c for c in index.chats_of(build_id) if c.get("build_id") == build_id]
    if not attached:
        return chat_routing.state([])
    chat = attached[0]
    return chat_routing.state(store.messages(chat["id"]), bool(chat.get("running")))


def _spoken(decision: dict) -> str:
    """The assistant's reply as plain text: what the chat shows compactly and what the agent reads as its own earlier turn."""
    route = decision.get("route")
    if route in ("generator", "edit"):
        what = [f"{c.get('label')}: {c.get('from')} -> {c.get('to')}" for c in decision.get("changes") or []]
        if route == "generator":
            what = [f"Built {decision.get('title') or decision.get('generator')} from the generator"]
        if decision.get("history"):
            what = [f"Went back one step ({decision['history']})"]
        text = "[Quick change made by the app's free fast path, not by you; the draft on the canvas changed accordingly] Applied to the draft (free): " + ("; ".join(what) or "no visible change") + "."
        return text + (" Notes: " + "; ".join(decision["notes"]) + "." if decision.get("notes") else "")
    if route == "clarify":
        return "[Quick-change helper, not the AI; nothing changed] " + str(decision.get("question") or "Can you say a little more?")
    est = decision.get("estimate") or {}
    text = f"This needs the AI ({decision.get('reason') or 'no rule fits'})"
    if est.get("eur_low") is not None:
        text += f", about EUR {est['eur_low']:g}-{est.get('eur_high', est['eur_low']):g}"
    spec = json.dumps(decision.get("spec") or {}, separators=(",", ":"), ensure_ascii=False)[:900]
    return text + f". Waiting for the person to confirm. The app's reading of the request (spec): {spec}"


def _persist_turn(store, build_id: str, text: str, decision: dict, took_ms: float) -> str:
    chat = attached_chat(store, build_id)
    store.add_message(chat["id"], {"role": "user", "content": text, "_fast_path": {"by": "person"}})   # a quick-change turn
    keep = ("route", "title", "generator", "changes", "notes", "history", "question", "choices", "reason", "estimate", "spec",
            "understood_by", "replaced_manual_edits", "pending", "highlight", "preset", "closest", "final")
    meta = {k: decision[k] for k in keep if decision.get(k) is not None}
    meta.update(cost_eur=0.0, took_ms=round(took_ms))
    store.add_message(chat["id"], {"role": "assistant", "content": _spoken(decision), "_fast_path": meta})
    return chat["id"]


def _last_user_text(store, build_id: str) -> Optional[str]:
    chat = attached_chat(store, build_id)
    for m in reversed(store.messages(chat["id"])):
        if m.get("role") == "user" and isinstance(m.get("content"), str) and not m.get("_hidden"):
            return m["content"]
    return None


@router.post("/api/builds/{build_id}/draft/describe")
async def draft_describe(build_id: str, body: Describe):
    """The fast path on the build. While the draft still matches the build's generator source, a generator or edit answer
    is regenerated into the draft. Otherwise (no generator, or the draft moved away from it: manual edits, an accepted AI
    proposal, an import) recolour/remove words change the model IN PLACE by role or selection, and nothing is ever
    regenerated over the person's work unless they say "regenerate"/"start over" (or confirm). needs_ai and clarify are
    returned as they are, with nothing spent and the draft untouched. The turn (the text and the answer, cost 0) is saved
    in the build's chat, so the chat is the whole history."""
    if not body.text.strip():
        raise HTTPException(422, "text is empty")
    started = time.monotonic()
    store = get_store()
    record = _check(store, build_id)
    registry = await generator_bridge.ensure()
    context = build_context(store, build_id, record, body.selection)
    current = context.pop("current")
    if current and registry and not any(e["id"] == current["generator"] for e in registry["entries"]):
        current = None
    previous = body.previous_text if body.previous_text is not None else (_last_user_text(store, build_id) if body.persist else context["previous"])
    context["previous"] = previous
    mode = _chat_state(store, build_id)
    if mode["mode"] == "ai" and not body.quick:      # the agent is engaged: it gets the message, nothing is applied or saved here
        return {"route": "agent", "chat_mode": "ai", "reason": "The AI is working on this build with you",
                "draft": _draft(store, build_id) if _record(store, build_id) is not None else None}
    entry = next((e for e in (registry or {}).get("entries", []) if current and e["id"] == current["generator"]), None)
    typed = understand._key(body.text)
    messages = _chat_messages(store, build_id)
    pending, explicit, force = _pending(messages), False, False
    selection = body.selection
    if pending and typed in ("keep my build", "no", "no, keep my build", "cancel"):
        decision = {"route": "clarify", "question": "OK, nothing changed.", "final": True, "choices": []}
    elif pending and typed in CONFIRM:
        if pending["kind"] == "role_edit":
            decision = {"route": "edit", "role_edits": pending["edits"], "changes": [], "notes": []}
            selection, force = pending.get("selection") or selection, True
        else:
            decision, explicit = pending["decision"], True
    elif EXPLICIT_REGENERATE.match(body.text.strip()):
        source = _source_now(store, build_id, record)
        known = source and registry and next((e for e in registry["entries"] if e["id"] == source["id"]), None)
        if known:
            decision, explicit = {"route": "generator", "generator": known["id"], "title": known["title"], "params": source["params"],
                                  "notes": ["regenerated from the settings; edits made since were replaced"], "cost_eur": 0.0}, True
        else:
            decision = {"route": "clarify", "question": "This build has no generator to regenerate from. Describe a new model to start over, or keep editing.",
                        "choices": STARTERS, "final": True}
    else:
        shortcut = fast_path._rules_spec(body.text, current, context, previous)[0]
        if not shortcut and not chat_routing.whole_edit_command(body.text) and chat_routing.is_continuation(body.text, mode["agent_text"]):
            decision = {"route": "clarify", "generic": True}          # a reply or follow-up, not a command: never a colour/size guess
        else:
            decision = await fast_path.describe(body.text, current=current, build_id=build_id, previous_text=previous, context=context)
    if decision.get("route") == "clarify" and decision.get("generic"):
        title = record.get("title") or build_id
        draft_now = _draft(store, build_id) if _record(store, build_id) is not None else None
        decision = {**decision, **chat_routing.contextual_clarify(
            title=title, entry=entry, params=(current or {}).get("params"), has_agent=mode["has_agent"],
            empty=not current and not builds.versions(build_id) and not (draft_now or {}).get("changes"))}
    if decision.get("route") == "clarify" and not decision.get("choices") and not decision.get("generic") and not decision.get("final"):
        decision["choices"] = TWEAKS if (current or context["has_model"]) else STARTERS
    if decision.get("route") == "generator" and not current and context["has_model"] and not explicit:
        # a whole new model over a draft that is not the generator's own: ask first, never replace silently
        decision = {"route": "clarify", "final": True, "choices": ["Yes, start over", "Keep my build"],
                    "question": f"This build already has {context['parts'] or 'some'} parts. Start over with {decision.get('title') or decision.get('generator')}? "
                                "Your current model stays in its versions and you can Undo.",
                    "pending": {"kind": "decision", "decision": decision}, "notes": decision.get("notes") or []}
    is_role = decision.get("route") == "edit" and (decision.get("role_edits") is not None or (decision.get("history") and not decision.get("generator")))
    if is_role:
        answer = await _role_edit(store, build_id, decision, selection, force)
    elif decision.get("route") in ("generator", "edit"):
        done = await _regenerate(store, build_id, decision["generator"], decision.get("params") or {})
        answer = {**decision, "replaced_manual_edits": done["replaced_manual_edits"], "draft": done["draft"]}
    else:
        answer = {**decision, "draft": _draft(store, build_id) if _record(store, build_id) is not None else None}
    answer["chat_mode"] = "quick"
    if body.persist:
        answer["chat_id"] = _persist_turn(store, build_id, body.text, answer, (time.monotonic() - started) * 1000)
        answer["took_ms"] = round((time.monotonic() - started) * 1000)
    return answer


@router.post("/api/builds/{build_id}/chat/note")
async def chat_note(build_id: str, body: Note):
    """A line in the build's chat that is for the person only (e.g. "Cancelled. Nothing was spent."): not sent to the AI."""
    store = get_store()
    _check(store, build_id)
    chat = attached_chat(store, build_id)
    store.add_message(chat["id"], {"role": "assistant", "_ui_only": True, "_notice": True, "content": body.text})
    return {"chat_id": chat["id"]}


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
