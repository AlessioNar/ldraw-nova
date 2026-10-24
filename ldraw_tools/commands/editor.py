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

import collections
import functools
import hashlib
import json
import os
import threading
import types
from pathlib import Path

import jsonschema
from ldraw.errors import PartError

from ..builder import check_schema
from ..common import CACHE, atomic_write, dumps
from ..editor import apply, import_mpd, inverse, rebuild, usage
from .base import Command

MAX_HISTORY = 100                       # undo batches kept per session
MAX_CACHED_IMPORTS = 60                 # imported models kept (newest by modification time)
SKELETON = {"version": 1, "author": "LDraw Nova editor", "sections": [
    {"name": "model.ldr", "description": "Manual editor model", "steps": [
        [{"id": "base", "ref": "3005.dat", "colour": 4, "at": [0, 0, 0], "purpose": "delete or build on this brick"}]]}]}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@functools.lru_cache(maxsize=1)
def _toolkit_version():
    from ..pipeline import toolkit_version
    return toolkit_version()


def _import_dir():
    return CACHE / "editor-imports"


def _import_key(source_text, library, parts):
    """Content hash of an imported model: its text, the toolkit that imports it and the parts library it is read with."""
    where = str(library if library is not None else parts.path)
    return _sha("|".join((_toolkit_version(), where, source_text)))


def _import_cached(key):
    """The stored import (plan, warnings, rows, mpd) for this key, or None (missing, unreadable or stale)."""
    try:
        found = json.loads((_import_dir() / f"{key}.json").read_text(encoding="utf-8"))
        return found if all(k in found for k in ("plan", "warnings", "rows", "mpd")) else None
    except (OSError, ValueError):
        return None


def _import_store(key, entry):
    """Keep an import for the next open of the same file; failures to write are not the editor's problem."""
    try:
        folder = _import_dir()
        atomic_write(folder / f"{key}.json", json.dumps(entry, ensure_ascii=False, allow_nan=False))
        old = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[MAX_CACHED_IMPORTS:]
        for path in old:
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _rows(plan, parts):
    return sorted((part, colour, qty) for (part, colour), qty in usage(plan, parts).items())


def _load(plan_path, state_path):
    """The committed pair, self-healing after a crash between writes.

    The state file is written last; if the plan on disk is not the one the state
    committed, an edit was applied but not acknowledged. The plan itself is still
    schema-valid (edits validate before any write), so it becomes the truth and
    the stale history is dropped.
    """
    text = Path(plan_path).read_text(encoding="utf-8")
    plan, state = json.loads(text), _read(state_path)
    # The file is what `_commit` wrote (dumps(plan) + newline): hashing it as read avoids encoding the plan again.
    # A file that was edited by hand (other whitespace) falls back to the plan's own canonical encoding.
    recorded = state.get("plan_sha256")
    if recorded is not None and recorded != _sha(text.removesuffix("\n")) and recorded != _sha(dumps(plan)):
        state = {"revision": state.get("revision", 0) + 1, "undo": [], "redo": []}
    state.setdefault("revision", 0)
    state.setdefault("undo", [])
    state.setdefault("redo", [])
    return plan, state


def _commit(plan_path, state_path, mpd_path, plan, state, rows, mpd_text):
    plan_text = dumps(plan)
    state = {"revision": state["revision"], "undo": state["undo"][-MAX_HISTORY:],
             "redo": state["redo"], "used": rows, "name": state.get("name"), "warnings": state.get("warnings"),
             "plan_sha256": _sha(plan_text)}
    atomic_write(plan_path, plan_text + "\n")
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
        warnings, cached = [], None
        if args.from_mpd:
            source_text = Path(args.from_mpd).read_text(encoding="utf-8", errors="replace")
            key = _import_key(source_text, library, parts)
            cached = _import_cached(key)
            if cached is not None:        # this exact model was imported before: its plan and preview are known
                plan, warnings, rows, mpd_text = (cached[k] for k in ("plan", "warnings", "rows", "mpd"))
                from .. import incremental
                incremental.remember(incremental.fingerprint(plan), mpd_text)
            else:
                plan, warnings = import_mpd(source_text, parts)
        else:
            plan = _read(args.source) if args.source else SKELETON
        if cached is None:
            check_schema(plan)
            rows = _rows(plan, parts)
            mpd_text = rebuild(plan, parts)
            if args.from_mpd:
                _import_store(key, dict(plan=plan, warnings=warnings, rows=rows, mpd=mpd_text))
        state = {"revision": 1, "undo": [], "redo": [], "name": args.name, "warnings": warnings or None}
        _commit(plan_path, state_path, mpd_path, plan, state, rows, mpd_text)
        return _report(True, state, plan=plan, used=rows, mpd=mpd_text, name=state["name"], warnings=warnings)

    plan, state = _load(plan_path, state_path)
    if args.base_revision is not None and args.base_revision != state["revision"]:
        return _report(False, state, conflict=True, error="The plan changed elsewhere; reload and retry")

    if args.action == "apply":
        edits = _read(args.edits)
        inverses = list(reversed(inverse(plan, edits, parts)))      # undo runs them last-first: a later edit may need an earlier one's part
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


def prewarm(parts, plan):
    """Load the geometry of every part the plan uses, on a background thread, most used first.

    The first load of a part type costs far more than the edits that follow (it resolves the part's files and
    its connection metadata); a snap inspects placements of many types, so without this the first snap of a
    session pays for each of them. A resident worker has the time while the person looks at the model.
    """
    if os.environ.get("LDRAW_NOVA_EDITOR_PREWARM") == "0":
        return None
    sections = {section["name"].casefold() for section in plan["sections"]}
    uses = collections.Counter(entry["ref"] for section in plan["sections"] for step in section["steps"] for entry in step
                               if entry["ref"].casefold() not in sections and not entry["ref"].startswith("@"))

    def load():
        for ref, _ in uses.most_common():
            try:
                parts.geometry(ref.rsplit(".", 1)[0])
            except Exception:      # a part with no geometry is the build's problem, not the warm-up's
                continue
    thread = threading.Thread(target=load, name="editor-prewarm", daemon=True)
    thread.start()
    return thread


def run_request(request, library, parts):
    """One editor action for a resident worker: `run_editor` with the CLI's arguments as a JSON object.

    The request has `action` (open|apply|undo|redo), the `plan`/`state`/`mpd` paths and the options the CLI
    takes (`base_revision`, `edits`, `owned`, `mode`, `source`, `from_mpd`, `name`). The reply is the CLI's
    report plus `exit_code` (0 done, 1 refused, 2 failed); the plan and the preview are on disk, so unless
    `full` is set they are left out of the reply. Failures the CLI turns into exit code 2 come back the same way.
    """
    names = ("action", "plan", "state", "mpd", "base_revision", "edits", "owned", "mode", "source", "from_mpd", "name")
    args = types.SimpleNamespace(**{k: request.get(k) for k in names})
    args.mode = args.mode or "off"
    try:
        report, status = run_editor(args, library, parts)
    except (OSError, ValueError, PartError, RecursionError, jsonschema.ValidationError) as exc:
        report, status = dict(checks_passed=False, error=str(exc), error_type=type(exc).__name__), 2
    if args.action == "open" and status == 0 and report.get("plan"):
        prewarm(parts, report["plan"])
    if not request.get("full"):
        report = {k: v for k, v in report.items() if k not in ("plan", "mpd")}
    return {**report, "exit_code": status}


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
            s.add_argument("--from-mpd", help="An MPD/LDR model file to import as the plan (instead of --from)")
            s.add_argument("--name", help="A name for the model, used when publishing")
        if action == "apply":
            s.add_argument("--edits", required=True, help="JSON file with the list of edit operations")
            s.add_argument("--owned", help="JSON file [[part, colour, qty], ...] of the chat's inventories")
            s.add_argument("--mode", choices=("off", "prefer", "only"), default="off",
                           help="off: no inventory check; only: refuse a short edit; prefer: flag it")


COMMANDS = {"editor": Command(register, run_editor, needs_parts=True)}
