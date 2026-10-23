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


async def t_editor_state(ctx):
    try:
        view = _api()._view(ctx.store, ctx.chat_id)
    except HTTPException as exc:
        return _result(_error(exc))
    data = _summary(view)
    data["bounds"] = _bounds(view["plan"])
    return _result(data)


async def t_editor_edit(ctx, edits: list, base_revision: int):
    if not isinstance(edits, list) or not edits or len(edits) > _api().MAX_EDITS:
        return _result({"ok": False, "error": f"edits must be a list of 1-{_api().MAX_EDITS} operations"})
    try:
        view = await _api().apply_edits(ctx.store, ctx.chat_id, edits, int(base_revision))
    except HTTPException as exc:
        return _result(_error(exc))
    ctx.emit("editor", {"revision": view["revision"], "edits": len(edits)})
    data = _summary(view)
    data.update(ok=True, applied=len(edits))
    return _result(data)


async def t_editor_open(ctx, plan: str, name: str | None = None):
    try:
        view = await _api().open_session(ctx.store, ctx.chat_id, plan, name)
    except HTTPException as exc:
        return _result(_error(exc))
    ctx.emit("editor", {"revision": view["revision"], "opened": True})
    data = _summary(view)
    data["ok"] = True
    return _result(data)


def _schema(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}}


SCHEMAS = {
    "editor_open": _schema("editor_open",
        "Save a plan JSON you built under output/ as this chat's editor plan, so the person can open and edit it in 3D.",
        {"plan": {"type": "string", "description": "Plan path under output/"}, "name": {"type": "string"}}, ["plan"]),
    "editor_state": _schema("editor_state",
        "Compact summary of the plan the person is editing: revision, piece counts, bounds and parts still available. "
        "Call it before editor_edit and after a conflict.", {}, []),
    "editor_edit": _schema("editor_edit",
        "Edit the editor plan in one batch (all or nothing). Ops: add{section,part,colour,position:[x,y,z]}, move{id,position}, "
        "rotate{id,axis,degrees}, recolour{id,colour}, delete{id}, snap{id,to}. base_revision: the revision from editor_state. "
        "A conflict means the person changed the plan: call editor_state and retry.",
        {"edits": {"type": "array", "items": {"type": "object"}}, "base_revision": {"type": "integer"}},
        ["edits", "base_revision"]),
}
FUNCTIONS = {"editor_open": t_editor_open, "editor_state": t_editor_state, "editor_edit": t_editor_edit}
