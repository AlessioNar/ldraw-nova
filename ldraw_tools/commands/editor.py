"""The manual editor's session operations, one process per request.

The web backend never imports the toolkit: it runs `ldraw-agent editor ...`
through its sandbox and reads the JSON report from stdout. The plan file is the
source of truth; the state file beside it holds the revision, the undo/redo
history (inverse operations, not plan copies) and the plan's used-piece rows.

Exit codes: 0 the action succeeded, 1 the action was refused (conflict, inventory
shortfall, empty history), 2 an exception (invalid edits, failed rebuild).
Nothing is written when an action is refused or fails, so the last good plan,
preview and history on disk always belong together.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..builder import check_schema
from ..common import atomic_write, dumps
from ..editor import apply, inverse, rebuild, usage
from .base import Command

MAX_HISTORY = 100                       # undo batches kept per session
SKELETON = {"version": 1, "author": "LDraw Nova editor", "sections": [
    {"name": "model.ldr", "description": "Manual editor model", "steps": [
        [{"id": "base", "ref": "3005.dat", "colour": 4, "at": [0, 0, 0], "purpose": "delete or build on this brick"}]]}]}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(plan, parts):
    return sorted((part, colour, qty) for (part, colour), qty in usage(plan, parts).items())


def _load(plan_path, state_path):
    """The committed pair, self-healing after a crash between writes.

    The state file is written last; if the plan on disk is not the one the state
    committed, an edit was applied but not acknowledged. The plan itself is still
    schema-valid (edits validate before any write), so it becomes the truth and
    the stale history is dropped.
    """
    plan, state = _read(plan_path), _read(state_path)
    digest = hashlib.sha256(dumps(plan).encode()).hexdigest()
    if state.get("plan_sha256") not in (None, digest):
        state = {"revision": state.get("revision", 0) + 1, "undo": [], "redo": []}
    state.setdefault("revision", 0)
    state.setdefault("undo", [])
    state.setdefault("redo", [])
    return plan, state


def _commit(plan_path, state_path, mpd_path, plan, state, rows, mpd_text):
    state = {"revision": state["revision"], "undo": state["undo"][-MAX_HISTORY:],
             "redo": state["redo"], "used": rows, "name": state.get("name"),
             "plan_sha256": hashlib.sha256(dumps(plan).encode()).hexdigest()}
    atomic_write(plan_path, dumps(plan) + "\n")
    atomic_write(mpd_path, mpd_text)
    atomic_write(state_path, dumps(state) + "\n")


def _report(ok, state, **extra):
    return dict(ok=ok, revision=state["revision"], undo_depth=len(state["undo"]),
                redo_depth=len(state["redo"]), **extra), 0 if ok else 1


def _short(rows, owned):
    result = []
    for part, colour, needed in rows:
        have = owned.get((str(part).casefold(), colour), 0)
        if needed > have:
            result.append([part, colour, needed, have])
    return result


def run_editor(args, library, parts):
    plan_path, state_path, mpd_path = (Path(p) for p in (args.plan, args.state, args.mpd))
    if args.action == "open":
        if plan_path.exists() or state_path.exists():
            raise ValueError("This chat already has an editor plan")
        plan = _read(args.source) if args.source else SKELETON
        check_schema(plan)
        rows = _rows(plan, parts)
        mpd_text = rebuild(plan, parts)
        state = {"revision": 1, "undo": [], "redo": [], "name": args.name}
        _commit(plan_path, state_path, mpd_path, plan, state, rows, mpd_text)
        return _report(True, state, plan=plan, used=rows, mpd=mpd_text, name=state["name"])

    plan, state = _load(plan_path, state_path)
    if args.base_revision is not None and args.base_revision != state["revision"]:
        return _report(False, state, conflict=True, error="The plan changed elsewhere; reload and retry")

    if args.action == "apply":
        edits = _read(args.edits)
        inverses = inverse(plan, edits, parts)
        candidate = apply(plan, edits, parts)
        rows = _rows(candidate, parts)
        flagged = []
        if args.owned and args.mode in ("prefer", "only"):
            owned = {(str(p).casefold(), c): q for p, c, q in _read(args.owned)}
            flagged = _short(rows, owned)
            if args.mode == "only" and flagged:
                return _report(False, state, error="The inventory cannot cover this edit", short=flagged)
        mpd_text = rebuild(candidate, parts)
        state = {**state, "undo": [*state["undo"], {"forward": edits, "inverse": inverses}], "redo": [],
                 "revision": state["revision"] + 1}
        _commit(plan_path, state_path, mpd_path, candidate, state, rows, mpd_text)
        return _report(True, state, plan=candidate, used=rows, mpd=mpd_text, short=flagged)

    if args.action == "undo":
        if not state["undo"]:
            return _report(False, state, error="Nothing to undo")
        batch = state["undo"][-1]
        candidate = apply(plan, batch["inverse"], parts)
        rows = _rows(candidate, parts)
        mpd_text = rebuild(candidate, parts)
        state = {**state, "undo": state["undo"][:-1], "redo": [*state["redo"], batch],
                 "revision": state["revision"] + 1}
        _commit(plan_path, state_path, mpd_path, candidate, state, rows, mpd_text)
        return _report(True, state, plan=candidate, used=rows, mpd=mpd_text)

    if not state["redo"]:
        return _report(False, state, error="Nothing to redo")
    batch = state["redo"][-1]
    candidate = apply(plan, batch["forward"], parts)
    rows = _rows(candidate, parts)
    mpd_text = rebuild(candidate, parts)
    state = {**state, "undo": [*state["undo"], batch], "redo": state["redo"][:-1],
             "revision": state["revision"] + 1}
    _commit(plan_path, state_path, mpd_path, candidate, state, rows, mpd_text)
    return _report(True, state, plan=candidate, used=rows, mpd=mpd_text)


def register(commands):
    c = commands.add_parser("editor", help="Manual editor sessions: open, apply, undo and redo a chat's plan")
    c.add_argument("--report", help="Also write the JSON report to this file")
    sub = c.add_subparsers(dest="action", required=True)
    for action in ("open", "apply", "undo", "redo"):
        s = sub.add_parser(action)
        s.add_argument("--plan", required=True, help="The chat's plan file (output/editor/plan.json)")
        s.add_argument("--state", required=True, help="The session state file beside it")
        s.add_argument("--mpd", required=True, help="The preview MPD file beside it")
        s.add_argument("--base-revision", type=int, help="Refuse the change when the session moved on")
        if action == "open":
            s.add_argument("--from", dest="source", help="An existing plan file to open (else a one-brick skeleton)")
            s.add_argument("--name", help="A name for the model, used when publishing")
        if action == "apply":
            s.add_argument("--edits", required=True, help="JSON file with the list of edit operations")
            s.add_argument("--owned", help="JSON file [[part, colour, qty], ...] of the chat's inventories")
            s.add_argument("--mode", choices=("off", "prefer", "only"), default="off",
                           help="off: no inventory check; only: refuse a short edit; prefer: flag it")


COMMANDS = {"editor": Command(register, run_editor, needs_parts=True)}
