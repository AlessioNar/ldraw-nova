"""The build workspace's bookkeeping: the draft's metadata, parts counted from model files, the parts check against
the person's whole collection, and AI proposals. See docs/plans/done/build-workspace/CONTRACT.md.

The draft IS the build's editor session (routers/editor.py, space `builds`); this module only keeps the files beside it:

    data/builds/<slug>/draft.json        {base_version, base_file, base_revision, saved_revision, updated_at}
    data/builds/<slug>/proposals/<id>.mpd + <id>.json   a chat's published model waiting for review
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Optional

import collection_store

PROPOSAL_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_PROPOSALS = 20
MAX_CACHED_CHECKS = 128
_checks: OrderedDict = OrderedDict()


# --- draft.json ----------------------------------------------------------------------------------------------

def draft_path(store, key: str) -> Path:
    return store.chat_dir(key) / "draft.json"


def _slug(key: str) -> str:
    return key[len("b-"):] if key.startswith("b-") else key


def read_draft(store, key: str) -> Optional[dict]:
    if store.pg:
        import pg_builds
        return pg_builds.read_draft(_slug(key))
    try:
        return json.loads(draft_path(store, key).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_draft(store, key: str, record: dict) -> dict:
    record = {**record, "updated_at": time.time()}
    if store.pg:
        import pg_builds
        pg_builds.write_draft(_slug(key), record)
        return record
    path = draft_path(store, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return record


def base_fields(view: dict) -> dict:
    """The draft's starting point: the revision and undo depth of the session when it was opened, saved or discarded."""
    return {"base_revision": view["revision"], "saved_revision": view["revision"],
            "base_undo_depth": view.get("undo_depth", 0), "forked": False, "drift": 0}


def touch_draft(store, key: str, kind: str = "edit", view: Optional[dict] = None) -> None:
    """An edit, undo or redo happened: remember when, and keep the net change count (only for a build with a draft).

    changes is the distance in undo steps from the base. While the session only walks along the history it started
    with that is |undo_depth - base|; a new edit made below the base forks it, after which the steps undone below
    the fork are added (`drift`), so edit + undo is clean and a forked state is never reported clean."""
    record = read_draft(store, key)
    if record is None:
        return
    if view is not None:
        depth, base = view.get("undo_depth", 0), record.get("base_undo_depth", 0)
        if not record.get("forked"):
            if kind == "edit" and depth - 1 < base:
                record.update(forked=True, drift=base - (depth - 1), base_undo_depth=depth - 1)
        elif depth < base:
            record.update(drift=record.get("drift", 0) + base - depth, base_undo_depth=depth)
    write_draft(store, key, record)


def draft_summary(record: dict, view: dict) -> dict:
    """The `draft` object of the API: how far the session is from the version it started on."""
    revision, depth = view.get("revision", 0), view.get("undo_depth", 0)
    base = record.get("base_undo_depth", depth)
    changes = record.get("drift", 0) + max(depth - base, 0) if record.get("forked") else abs(depth - base)
    return {"base_version": record.get("base_version"), "changes": changes,
            "revision": revision, "undo_depth": view.get("undo_depth", 0), "redo_depth": view.get("redo_depth", 0),
            "updated_at": record.get("updated_at")}


# --- parts of a model file -----------------------------------------------------------------------------------

def _files(text: str) -> tuple[dict, str]:
    """{file name: its type-1 lines split into words} and the root file's name (the first FILE, else "")."""
    files: dict = {}
    current, root = "", None
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "0":
            if len(words) >= 3 and words[1] == "FILE":
                current = line.split(None, 2)[2].strip().replace("\\", "/").lower()
                files.setdefault(current, [])
                root = root if root is not None else current
            elif words[1:2] == ["NOFILE"]:
                current = ""
        elif words[0] == "1" and len(words) >= 15:
            files.setdefault(current, []).append(words)
            root = root if root is not None else current
    return files, root or ""


def part_usage(text: str) -> dict:
    """{(part number without .dat, colour): quantity}: leaf parts counted per placement, submodels expanded,
    colour 16 taking the colour of the placement that references it. Same keys as the editor's `used` rows."""
    files, root = _files(text)
    memo: dict = {}

    def count(name: str, inherit: int, stack: tuple) -> Counter:
        key = (name, inherit)
        if key in memo:
            return memo[key]
        total: Counter = Counter()
        for words in files.get(name, []):
            try:
                colour = int(words[1])
            except ValueError:
                continue
            colour = inherit if colour == 16 else colour
            ref = " ".join(words[14:]).replace("\\", "/").lower()
            if ref in files:
                if ref not in stack and len(stack) < 40:
                    for item, qty in count(ref, colour, (*stack, ref)).items():
                        total[item] += qty
            else:
                total[(ref.rsplit("/", 1)[-1].removesuffix(".dat"), colour)] += 1
        memo[key] = total
        return total

    return dict(count(root, 16, (root,)))


_file_usage: OrderedDict = OrderedDict()


def file_usage(path: Path) -> dict:
    """part_usage of a model file, remembered until the file changes."""
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _file_usage:
        _file_usage[key] = part_usage(path.read_text(encoding="utf-8", errors="replace"))
        while len(_file_usage) > MAX_CACHED_CHECKS:
            _file_usage.popitem(last=False)
    return _file_usage[key]


def rows_usage(rows) -> dict:
    """The editor state's `used` rows [[part, colour, qty], ...] as a usage map."""
    return {(str(part).casefold(), colour): qty for part, colour, qty in rows or []}


def placements(text: str) -> Counter:
    """Every type-1 line of every file as (file, ref, colour, 12 numbers rounded), for comparing two models."""
    files, _ = _files(text)
    found: Counter = Counter()
    for name, lines in files.items():
        for words in lines:
            try:
                found[(name, " ".join(words[14:]).lower(), int(words[1]),
                       tuple(round(float(v), 1) for v in words[2:14]))] += 1
            except ValueError:
                continue
    return found


def diff_summary(before: str, after: str) -> dict:
    """{added, removed, moved}: placements new in `after`, gone from it, and the same part moved or turned."""
    old, new = placements(before), placements(after)
    gone, fresh = old - new, new - old
    pairs: dict = {}
    for side, counter in ((0, gone), (1, fresh)):
        for (name, ref, colour, _), qty in counter.items():
            pairs.setdefault((name, ref, colour), [0, 0])[side] += qty
    moved = sum(min(a, b) for a, b in pairs.values())
    return {"added": sum(fresh.values()) - moved, "removed": sum(gone.values()) - moved, "moved": moved}


# --- the parts check -----------------------------------------------------------------------------------------

def owned_parts() -> tuple[dict, str]:
    """All of the person's parts lists added together, exact rows only (the Pileometer review pieces are in
    `review`, not `rows`), and a signature that changes when any list does. Read through the collection store."""
    return collection_store.owned_totals()


def available_parts(exclude_build: Optional[str] = None) -> tuple[dict, str]:
    """Owned minus what built sets and reservations hold (not counting `exclude_build`'s own models), and its signature."""
    import reservations
    return reservations.available_totals(exclude_build)


def check(usage: dict, owned: Optional[dict] = None, signature: str = "") -> dict:
    """{owned, needed, missing: [{part, colour, need, have}]}: pieces the collection covers, pieces the model
    needs, and the (part, colour) pairs it is short of. Cached by the content of the usage and the lists."""
    if owned is None:
        owned, signature = owned_parts()
    digest = hashlib.sha256((signature + "\n" + json.dumps(sorted(usage.items()))).encode()).hexdigest()
    cached = _checks.get(digest)
    if cached is not None:
        _checks.move_to_end(digest)
        return cached
    missing, covered = [], 0
    for (part, colour), need in sorted(usage.items()):
        have = owned.get((part, colour), 0)
        covered += min(need, have)
        if need > have:
            missing.append({"part": part, "colour": colour, "need": need, "have": have})
    result = {"owned": covered, "needed": sum(usage.values()), "missing": missing}
    _checks[digest] = result
    while len(_checks) > MAX_CACHED_CHECKS:
        _checks.popitem(last=False)
    return result


# --- proposals -----------------------------------------------------------------------------------------------

def proposals_dir(store, slug: str) -> Path:
    return store.builds_dir / slug / "proposals"


def create_proposal(store, slug: str, mpd_text: str, chat_id: Optional[str], base_text: str = "",
                    cost_eur: float = 0.0, warnings: Optional[list] = None) -> dict:
    folder = proposals_dir(store, slug)
    folder.mkdir(parents=True, exist_ok=True)
    pid = secrets.token_hex(6)
    (folder / f"{pid}.mpd").write_text(mpd_text, encoding="utf-8")
    record = {"id": pid, "created_at": time.time(), "summary": diff_summary(base_text, mpd_text),
              "cost_eur": round(cost_eur, 4), "chat_id": chat_id, **({"warnings": warnings} if warnings else {})}
    if store.pg:
        import pg_builds
        try:
            pg_builds.add_proposal(slug, record)
        except Exception:
            (folder / f"{pid}.mpd").unlink(missing_ok=True)
            raise
    else:
        (folder / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")
    for old in list_proposals(store, slug)[:-MAX_PROPOSALS]:       # keep the newest few
        delete_proposal(store, slug, old["id"])
    return record


def list_proposals(store, slug: str) -> list:
    """Oldest first."""
    if store.pg:
        import pg_builds
        return [r for r in pg_builds.proposals(slug) if (proposals_dir(store, slug) / f"{r['id']}.mpd").is_file()]
    folder = proposals_dir(store, slug)
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if PROPOSAL_RE.match(str(record.get("id", ""))) and path.with_suffix(".mpd").is_file():
            found.append(record)
    return sorted(found, key=lambda r: r.get("created_at", 0))


def proposal_file(store, slug: str, pid: str) -> Optional[Path]:
    if not PROPOSAL_RE.match(pid or ""):
        return None
    path = proposals_dir(store, slug) / f"{pid}.mpd"
    if store.pg:
        import pg_builds
        return path if path.is_file() and pg_builds.has_proposal(slug, pid) else None
    return path if path.is_file() and path.with_suffix(".json").is_file() else None


def delete_proposal(store, slug: str, pid: str) -> None:
    if store.pg:
        import pg_builds
        pg_builds.remove_proposal(slug, pid)
    for suffix in (".mpd", ".json"):
        (proposals_dir(store, slug) / f"{pid}{suffix}").unlink(missing_ok=True)
