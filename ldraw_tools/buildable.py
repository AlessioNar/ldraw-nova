"""Which bundled set models can an inventory build, or nearly build?

Every model's physical BOM is expanded once and cached on disk, keyed by the model file's size and
mtime and by a signature of the parts library, so the first run over the whole catalogue takes minutes
and later runs take seconds.  The comparison with an inventory is inventory.match_bom, the same
counting `inventory check` uses.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from .common import CACHE, atomic_write, database_path, models_path, readonly_uri
from .inventory import colour_name, combine, describe, index_owned, match_bom, substituted_pieces

VERSION = 1
MIN_PARTS = 20
TOP_MISSING = 5
DEFAULT_BUDGET = 900        # seconds of BOM building per invocation; a rerun resumes where it stopped
SET_ID = re.compile(r"(\d+-\d+)")


def library_signature(library):
    """Changes with the library's part files (get_parts stamps them) and its location."""
    stamp = library.path.parent / "signature"
    text = stamp.read_text() if stamp.is_file() else str(library.path.stat().st_mtime_ns)
    return hashlib.sha256(f"{VERSION}|{library.path.resolve()}|{text}".encode()).hexdigest()[:16]


def cache_dir(library, base=None):
    return Path(base) if base else CACHE / "buildable" / library_signature(library)


def model_files(root=None):
    root = models_path(root)
    return root, sorted(p for p in root.rglob("*") if p.suffix.lower() in {".mpd", ".ldr"} and p.is_file())


def descriptions(database=None):
    """{model file name: description} from the discovery database; empty when it is unavailable."""
    try:
        with sqlite3.connect(readonly_uri(database_path(database)), uri=True) as db:
            return dict(db.execute("SELECT model, description FROM MODELS_DESCRIPTIONS"))
    except (sqlite3.Error, OSError):
        return {}


@dataclass
class Entry:
    name: str                                   # path relative to the models folder
    bom: dict | None = None                     # {(part, colour): qty} (colour None when unresolved); None when the model could not be parsed
    error: str = ""

    @property
    def total(self):
        return sum(self.bom.values()) if self.bom else 0


@dataclass
class Scan:
    entries: list[Entry] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)    # files whose BOM is not cached yet
    built: int = 0
    reused: int = 0
    total: int = 0

    @property
    def failed(self):
        return [e for e in self.entries if e.bom is None]


def _slot(directory, name):
    return Path(directory) / (hashlib.sha1(name.encode()).hexdigest()[:20] + ".json")


def _read(directory, name, stat):
    try:
        data = json.loads(_slot(directory, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("file") != name or data.get("size") != stat.st_size or data.get("mtime_ns") != stat.st_mtime_ns:
        return None
    if "bom" in data:
        return Entry(name, {(p, c): int(q) for p, c, q in data["bom"]})
    return Entry(name, None, str(data.get("error", "unreadable")))


def _write(directory, name, stat, entry):
    data = dict(file=name, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    if entry.bom is not None:
        data["bom"] = [[p, c, q] for (p, c), q in sorted(entry.bom.items(), key=lambda kv: (kv[0][0], kv[0][1] is not None, kv[0][1] or 0))]
    else:
        data["error"] = entry.error
    atomic_write(_slot(directory, name), json.dumps(data))


_LIBRARY = None


def _init_worker(root):
    global _LIBRARY
    from .common import get_parts
    _LIBRARY = get_parts(root)


def _build(path, library=None):
    """(bom rows, error) of one model; in a worker process the library is the one _init_worker loaded."""
    from .document import parse_source
    from .inventory import model_bom
    try:
        bom = model_bom(parse_source(path), library or _LIBRARY)
        return [[p, c, q] for (p, c), q in bom.items()], ""
    except Exception as exc:                    # a bad model is skipped and counted, never fatal
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"


def scan(library, *, root=None, cache=None, build=True, budget=DEFAULT_BUDGET, workers=None, progress=None):
    """Load every model's cached BOM; build the missing ones for at most `budget` seconds (resumable)."""
    root, files = model_files(root)
    directory = cache_dir(library, cache)
    directory.mkdir(parents=True, exist_ok=True)
    result = Scan(total=len(files))
    todo = []
    for path in files:
        name = path.relative_to(root).as_posix()
        entry = _read(directory, name, path.stat())
        if entry is None:
            todo.append((name, path))
        else:
            result.entries.append(entry)
            result.reused += 1
    if build and todo:
        _build_all(library, todo, result, directory, budget, workers or min(4, os.cpu_count() or 1), progress)
    else:
        result.pending = [name for name, _ in todo]
    return result


def _build_all(library, todo, result, directory, budget, workers, progress):
    started, last = time.monotonic(), 0.0

    def report(force=False):
        nonlocal last
        now = time.monotonic()
        if progress and (force or now - last >= 1.0):
            progress(len(result.entries), result.total)
            last = now

    def record(name, path, rows, error):
        entry = Entry(name, {(p, c): q for p, c, q in rows} if rows is not None else None, error)
        _write(directory, name, path.stat(), entry)
        result.entries.append(entry)
        result.built += 1
        report()

    report(True)
    queue = iter(todo)
    if workers <= 1:
        for name, path in queue:
            record(name, path, *_build(str(path), library))
            if time.monotonic() - started >= budget:
                break
    else:
        running = {}
        with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(str(library.path.parent.resolve()),)) as pool:
            def submit():
                item = next(queue, None) if time.monotonic() - started < budget else None
                if item is not None:
                    running[pool.submit(_build, str(item[1]))] = item

            for _ in range(workers * 2):
                submit()
            while running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    name, path = running.pop(future)
                    try:
                        rows, error = future.result()
                    except Exception as exc:    # a crashed worker costs only this model
                        rows, error = None, f"{type(exc).__name__}: {exc}"[:200]
                    record(name, path, rows, error)
                    submit()
    result.pending = [name for name, _ in queue]
    report(True)


def stderr_progress(done, total):
    print(f"buildable: {done}/{total} models", file=sys.stderr, flush=True)


def family(name):
    """The file a sub-variant belongs to: 8448-1_Racer.mpd -> 8448-1.mpd."""
    stem, _, ext = name.rpartition(".")
    base, sep, _ = stem.partition("_")
    return f"{base}.{ext}" if sep else None


def drop_subvariants(entries):
    """Entries minus sub-variants whose BOM fits inside their parent set model's (same file family); count dropped."""
    by_name = {e.name: e for e in entries if e.bom}
    kept, dropped = [], 0
    for e in entries:
        parent = by_name.get(family(e.name) or "")
        if e.bom and parent is not None and parent is not e and all(parent.bom.get(k, 0) >= q for k, q in e.bom.items()):
            dropped += 1
            continue
        kept.append(e)
    return kept, dropped


def evaluate(entry, owned, by_part):
    ok, short = match_bom(entry.bom, owned)
    return dict(entry=entry, needed=entry.total, matched=ok, short=short,
                missing=sum(n - h for _, _, n, h in short), substituted=substituted_pieces(entry.bom, by_part))


def rank(entries, inventories, *, library=None, descriptions=None, limit=20, min_parts=MIN_PARTS, max_parts=None,
         max_missing=None, max_missing_share=None, query="", substitute_colours=False, keep_subvariants=False):
    """Ranked buildable models plus the counts that explain what was left out.

    Completeness is matched / needed pieces; ties go to fewer missing pieces, then smaller models.  custom_pieces
    counts needed pieces on parts outside the LDraw library (custom or embedded part files): no inventory covers
    them, so they lower the completeness of the models that have them.
    """
    descriptions = descriptions or {}
    owned = combine(inventories)
    by_part = index_owned(owned)
    usable = [e for e in entries if e.bom]
    counts = dict(models=len(entries), parsed=len(usable), skipped=len(entries) - len(usable))
    counts["too_small"] = sum(1 for e in usable if e.total < min_parts)
    pool = [e for e in usable if e.total >= min_parts]
    counts["too_large"] = 0
    if max_parts is not None:
        counts["too_large"] = sum(1 for e in pool if e.total > max_parts)
        pool = [e for e in pool if e.total <= max_parts]
    counts["subvariants"] = 0
    if not keep_subvariants:
        pool, counts["subvariants"] = drop_subvariants(pool)
    tokens = query.casefold().split()
    if tokens:
        pool = [e for e in pool if all(t in f"{e.name} {descriptions.get(Path(e.name).name, '')}".casefold() for t in tokens)]
    scored = [evaluate(e, owned, by_part) for e in pool]
    scored = [s for s in scored if s["matched"] > 0]
    if max_missing is not None:
        scored = [s for s in scored if s["missing"] <= max_missing]
    if max_missing_share is not None:
        scored = [s for s in scored if s["missing"] <= max_missing_share * s["needed"]]
    scored.sort(key=lambda s: (-s["matched"] / s["needed"], s["missing"], s["needed"], s["entry"].name))
    counts["ranked"] = len(scored)
    results = []
    for s in scored[:limit]:
        name = s["entry"].name
        lines = sorted(s["short"], key=lambda l: (-(l[2] - l[3]), l[0], l[1] is not None, l[1] or 0))
        item = dict(file=name, id=Path(name).stem, set_id=(SET_ID.match(Path(name).stem) or [None])[0],
                    description=descriptions.get(Path(name).name, ""), pieces=s["needed"], matched=s["matched"],
                    completeness=round(s["matched"] / s["needed"], 4), missing=s["missing"], missing_lines=len(s["short"]),
                    custom_pieces=sum(q for (p, _), q in s["entry"].bom.items() if p not in library.by_code) if library is not None else 0,
                    top_missing=[dict(part=p, colour=c, colour_name=colour_name(c, library), description=describe(p, library),
                                      needed=n, owned=h, missing=n - h) for p, c, n, h in lines[:TOP_MISSING]])
        if substitute_colours:
            item.update(substituted_matched=s["substituted"], substituted_completeness=round(s["substituted"] / s["needed"], 4))
        results.append(item)
    return results, counts
