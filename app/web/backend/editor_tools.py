"""The agent's view of the chat's editor plan: editor_open, editor_state and editor_edit.

They share the editor routes' code (one lock, one revision counter, the same inventory gate), so an edit by the
person and one by the agent can never overwrite each other: a stale base revision is a conflict result and the
agent must call editor_state again. Results are short JSON, never the plan or the MPD.
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from store import get_store


def _api():
    from routers import editor      # lazy: the router imports tools, which imports this module
    return editor


LIMIT = 900                       # characters of any tool result (the brief asks for under 1000)
EDIT_TOOLS = {"editor_state", "editor_edit"}
OPEN_TOOLS = {"editor_open"}
ROWS = 12                         # parts rows shown by editor_state


def has_plan(chat_id: str | None) -> bool:
    return bool(chat_id) and _api()._session(get_store(), chat_id) is not None


def _result(data: dict):
    from tools import ToolResult
    text = json.dumps(data, separators=(",", ":"))
    if len(text) > LIMIT:
        text = text[:LIMIT - 12] + "...(cut)"
    return ToolResult(text)


def _bounds(plan: dict) -> dict:
    """Box around the placed parts' origins (not their full geometry)."""
    points = [e["at"] for s in plan.get("sections", []) for step in s.get("steps", []) for e in step
              if isinstance(e, dict) and isinstance(e.get("at"), list) and len(e["at"]) == 3]
    if not points:
        return {}
    return {"min": [min(p[i] for p in points) for i in range(3)], "max": [max(p[i] for p in points) for i in range(3)]}


def _summary(view: dict) -> dict:
    table = view["parts"]
    rows = table["rows"]
    out = {"revision": view["revision"], "pieces": sum(r["used"] for r in rows), "kinds": len(rows), "valid": True}
    if table["mode"] != "off":
        out["parts_mode"] = table["mode"]
        # tightest first: what the plan can still add before a part runs out ([part, colour, available])
        tight = sorted(rows, key=lambda r: r["available"])[:ROWS]
        out["available"] = [[r["part"], r["colour"], r["available"]] for r in tight]
        if table["short"]:
            out["short"] = [[r["part"], r["colour"], r["missing"]] for r in table["short"][:ROWS]]
    return out


def _error(exc: HTTPException) -> dict:
    detail = exc.detail
    if isinstance(detail, dict):
        data = {"ok": False, "error": str(detail.get("detail"))[:300]}
        if "revision" in detail:
            data.update(conflict=True, revision=detail["revision"],
                        hint="The plan changed elsewhere (the person edited it). Call editor_state and redo the edit.")
        if detail.get("short"):
            data["refused_short"] = [[p, c, need, have] for p, c, need, have in detail["short"][:ROWS]]
            data["hint"] = "Those parts are not available any more; pick other parts (my_parts)."
        return data
    return {"ok": False, "error": str(detail)[:300]}


# --- what the agent has seen of the plan (so it can tell what the person changed) ---------------------------

SEEN_FILE = "agent-seen.json"


def _entries(plan: dict):
    for section in plan.get("sections", []):
        for step in section.get("steps", []):
            for entry in step:
                if isinstance(entry, dict) and entry.get("id"):
                    yield section.get("name"), entry


def _part(entry: dict) -> str:
    ref = str(entry.get("ref") or "")
    return ref[:-4] if ref.lower().endswith(".dat") else ref


def _snapshot(plan: dict) -> dict:
    """entry id -> [signature, part, colour]; the signature changes when the entry is moved, rotated or recoloured."""
    return {e["id"]: [json.dumps([e.get("ref"), e.get("colour"), e.get("at"), e.get("snap")], separators=(",", ":")),
                      _part(e), e.get("colour")] for _, e in _entries(plan)}


def _seen_path(store, chat_id: str):
    return store.work_dir(chat_id) / "editor" / SEEN_FILE


def remember(store, chat_id: str, plan: dict, revision: int) -> None:
    """The agent has now seen this revision (it viewed, edited or opened it)."""
    path = _seen_path(store, chat_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"revision": revision, "entries": _snapshot(plan)}, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def _seen(store, chat_id: str) -> dict | None:
    try:
        data = json.loads(_seen_path(store, chat_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("entries"), dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def changes_since(seen: dict, plan: dict) -> dict:
    """{added: [[id, part, colour]], removed: [id], changed: [id]} between what the agent saw and the plan now."""
    now, then = _snapshot(plan), seen["entries"]
    return {"added": [[i, v[1], v[2]] for i, v in now.items() if i not in then],
            "removed": [i for i in then if i not in now],
            "changed": [i for i, v in now.items() if i in then and then[i][0] != v[0]]}


def turn_note(store, chat_id: str | None) -> str:
    """One line for the prompt's volatile tail when the person changed the plan since the agent last looked; else ''."""
    if not chat_id:
        return ""
    session = _api()._session(store, chat_id)
    if session is None:
        return ""
    plan, state, _ = session
    revision = state.get("revision", 0)
    seen = _seen(store, chat_id)
    if seen is None:
        return f"The chat has an editor plan (revision {revision}) you have not looked at yet; call editor_state."
    if seen.get("revision") == revision:
        return ""
    c = changes_since(seen, plan)
    return (f"The person edited the plan since you last looked: revision {seen.get('revision')} to {revision}, "
            f"{len(c['added'])} parts added, {len(c['removed'])} removed, {len(c['changed'])} changed. "
            "Call editor_state for the details before you edit or rebuild it.")


def _fit(data: dict, key: str, rows: list, spare: int = 40) -> None:
    """Put as many rows under data[key] as keep the result below LIMIT (the rest is counted in `<key>_more`)."""
    kept: list = []
    data[key] = kept
    for row in rows:
        kept.append(row)
        if len(json.dumps(data, separators=(",", ":"))) > LIMIT - spare:
            kept.pop()
            break
    if len(kept) < len(rows):
        data[f"{key}_more"] = len(rows) - len(kept)
    if not kept:
        del data[key]


async def t_editor_state(ctx, section: str | None = None, part: str | None = None, offset: int = 0):
    try:
        view = _api()._view(ctx.store, ctx.chat_id)
    except HTTPException as exc:
        return _result(_error(exc))
    plan = view["plan"]
    data = _summary(view)
    data["bounds"] = _bounds(plan)
    seen = _seen(ctx.store, ctx.chat_id)
    if seen is not None and seen.get("revision") != view["revision"]:
        c = changes_since(seen, plan)
        since = data["since"] = {"from": seen.get("revision"), "removed": len(c["removed"]), "changed": len(c["changed"])}
        for key, rows in (("added", c["added"]), ("removed_ids", c["removed"]), ("changed_ids", c["changed"])):
            if rows:
                _fit(since, key, rows[:15])
    wanted = str(part or "").lower().removesuffix(".dat")
    rows = [[e["id"], _part(e), e.get("colour")] for name, e in _entries(plan)
            if (not section or name == section) and (not wanted or _part(e).lower() == wanted)]
    data["entries_total"] = len(rows)
    _fit(data, "entries", rows[max(int(offset or 0), 0):])
    remember(ctx.store, ctx.chat_id, plan, view["revision"])
    return _result(data)


async def t_editor_edit(ctx, edits: list, base_revision: int):
    if not isinstance(edits, list) or not edits or len(edits) > _api().MAX_EDITS:
        return _result({"ok": False, "error": f"edits must be a list of 1-{_api().MAX_EDITS} operations"})
    try:
        view = await _api().apply_edits(ctx.store, ctx.chat_id, edits, int(base_revision))
    except HTTPException as exc:
        return _result(_error(exc))
    remember(ctx.store, ctx.chat_id, view["plan"], view["revision"])
    ctx.emit("editor", {"revision": view["revision"], "edits": len(edits)})
    data = _summary(view)
    data.update(ok=True, applied=len(edits))
    return _result(data)


async def t_editor_open(ctx, plan: str, name: str | None = None, replace: bool = False):
    try:
        view = await _api().open_session(ctx.store, ctx.chat_id, plan, name, bool(replace))
    except HTTPException as exc:
        return _result(_error(exc))
    remember(ctx.store, ctx.chat_id, view["plan"], view["revision"])
    ctx.emit("editor", {"revision": view["revision"], "opened": True})
    data = _summary(view)
    data["ok"] = True
    return _result(data)


def _schema(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}}


SCHEMAS = {
    "editor_open": _schema("editor_open",
        "Save a plan JSON you built under output/ as this chat's editor plan, so the person can open and edit it in 3D. "
        "If the chat already has an editor plan this is refused unless replace is true, which archives the current plan "
        "(under output/editor/archive/) and opens yours instead: use it only when your plan already contains the "
        "person's edits (editor_state shows them), or when they asked for a new model.",
        {"plan": {"type": "string", "description": "Plan path under output/"}, "name": {"type": "string"},
         "replace": {"type": "boolean", "description": "Archive the current editor plan and open this one"}}, ["plan"]),
    "editor_state": _schema("editor_state",
        "Compact summary of the plan the person is editing: revision, piece counts, bounds, parts still available, "
        "entries as [id, part, colour] (the ids editor_edit needs) and, when the person changed the plan since you last "
        "looked, a `since` block with what they added, removed and changed. Call it before editor_edit and after a "
        "conflict. Filters: section (name), part (number), offset (to page through entries).",
        {"section": {"type": "string"}, "part": {"type": "string"}, "offset": {"type": "integer"}}, []),
    "editor_edit": _schema("editor_edit",
        "Edit the editor plan in one batch (all or nothing). Ops: add{section,part,colour,position:[x,y,z]}, move{id,position}, "
        "rotate{id,axis,degrees}, recolour{id,colour}, delete{id}, snap{id,to}. base_revision: the revision from editor_state. "
        "A conflict means the person changed the plan: call editor_state and retry.",
        {"edits": {"type": "array", "items": {"type": "object"}}, "base_revision": {"type": "integer"}},
        ["edits", "base_revision"]),
}
FUNCTIONS = {"editor_open": t_editor_open, "editor_state": t_editor_state, "editor_edit": t_editor_edit}
