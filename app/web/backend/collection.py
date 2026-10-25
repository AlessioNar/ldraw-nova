"""The parts collection: the sum of the owner's sources, with one write path that keeps history.

A *source* is what the older code calls an inventory: `<INVENTORIES_DIR>/<id>.json` (plus the original `<id>.csv` for an
upload). The collection is not a new store, it is all sources added together, exactly as `inventory_ids` already works
for chats. Sources come in three kinds: `import` (a parts list upload, the default), `set` (the parts of a catalogue set,
materialised at add time) and `loose` (id "loose": manual and quick adds). Optional keys on the existing file format:
`kind`, `set`, `origin`, `schema`, `review` (colour-family pieces, never counted) and per-row `note`.

Every edit goes through `apply_ops` (or `replace_source` for a re-import) and appends one batch to the append-only log
`<id>.log.jsonl`; `undo` reverses the last undoable batch. Only exact `rows` are ever counted by the palette, the parts
check and "what can I build", so a "maybe" (review) piece can never inflate an answer.

All of this is plain JSON in the backend: the toolkit CLI (agent sandbox) only reads sources, only the backend writes.
"""
from __future__ import annotations

import copy
import re
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any

import collection_store as store
import inventories
import part_info
from inventories import InventoryError, LOOSE_ID

LOOSE_NAME = "Loose parts"
MAX_BATCHES = store.MAX_BATCHES   # history kept per source
MAX_OPS = 500
MAX_QTY = 1_000_000
MAX_NOTE = 200
MAX_PREVIEWS = 8
PREVIEW_TTL = 30 * 60
MAX_DIFF_LINES = 200
PART_RE = re.compile(r"[0-9a-z][0-9a-z_-]{0,39}")
HIDDEN_COLOURS = part_info.HIDDEN_COLOURS

_notes_lock = threading.Lock()


def part_key(part: Any) -> str:
    return str(part).strip().casefold().removesuffix(".dat")


# --- files -------------------------------------------------------------------

def _totals(data: dict) -> dict:
    rows = data.get("rows", [])
    return {"pieces": sum(r["qty"] for r in rows), "distinct_parts": len({r["part"] for r in rows}),
            "distinct_colours": len({r["colour"] for r in rows}),
            "unmapped_pieces": sum(u["qty"] for u in data.get("unmapped_parts", [])) + sum(u["qty"] for u in data.get("unmapped_colours", []))}


def _save(data: dict) -> None:
    """Writes a source (caller holds store.lock). First edit upgrades the file to schema 2."""
    data["schema"] = 2
    data["rows"].sort(key=lambda r: (str(r["part"]), r["colour"]))
    data["totals"] = _totals(data)
    store.save(data)


def _read_log(source_id: str) -> list[dict]:
    return store.read_log(source_id)


def _append_log(source_id: str, new: list[dict]) -> None:
    store.append_log(source_id, new)


def _next_batch(entries: list[dict]) -> int:
    return max((e.get("batch", 0) for e in entries), default=0) + 1


def _stamp(entries: list[dict], batch: int, note: str = "") -> list[dict]:
    ts = round(time.time(), 3)
    return [{"ts": ts, "batch": batch, **({"note": note} if note else {}), **e} for e in entries]


# --- sources -----------------------------------------------------------------

def new_loose() -> dict:
    return {"id": LOOSE_ID, "name": LOOSE_NAME, "created_at": time.time(), "source_filename": None, "kind": "loose", "rows": [],
            "unmapped_parts": [], "unmapped_colours": [], "warnings": [], "schema": 2}


def _editable(source_id: str, create: bool = False) -> dict:
    """A deep copy of a source to edit (the loader's cached dict is shared and must never be changed)."""
    if source_id == LOOSE_ID and not store.exists(LOOSE_ID):
        if not create:
            raise InventoryError(404, "no such inventory")
        return new_loose()
    return store.load_copy(source_id)


def sources() -> list[dict]:
    """Every source, newest first; the Loose parts source is listed (empty) even before the first add."""
    found = inventories.list_inventories()
    if not any(s["id"] == LOOSE_ID for s in found):
        found.append(inventories.summary(new_loose()) | {"virtual": True})
    return found


def _clean_part(value: Any) -> str:
    part = part_key(value) if isinstance(value, str) else ""
    if not PART_RE.fullmatch(part):
        raise InventoryError(400, f"Not a part number: {str(value)[:40]!r}")
    return part


def _clean_colour(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9999 or value in HIDDEN_COLOURS:
        raise InventoryError(400, f"Not a colour code: {str(value)[:20]!r}")
    names = part_info.colour_names()
    if names and value not in names:
        raise InventoryError(400, f"Colour {value} is not an LDraw colour")
    return value


def _clean_qty(value: Any, low: int, what: str = "quantity") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= MAX_QTY:
        raise InventoryError(400, f"The {what} must be a whole number from {low} to {MAX_QTY:,}")
    return value


def _check_known_part(part: str) -> None:
    known = part_info.meta()
    if known and part not in known:
        raise InventoryError(400, f"Part {part} is not in the LDraw library")


def _match(row: dict, part: str, colour: int) -> bool:
    return part_key(row["part"]) == part and row["colour"] == colour


def _qty_of(data: dict, part: str, colour: int) -> int:
    return sum(r["qty"] for r in data["rows"] if _match(r, part, colour))


def _put_rows(data: dict, part: str, colour: int, rows: list[dict]) -> None:
    data["rows"] = [r for r in data["rows"] if not _match(r, part, colour)] + copy.deepcopy(rows)


def _set_qty(data: dict, part: str, colour: int, qty: int, template: dict | None = None) -> list[dict]:
    """Makes the total of (part, colour) `qty` (rows with a note or location keep theirs); returns the rows it replaced."""
    before = [r for r in data["rows"] if _match(r, part, colour)]
    kept = copy.deepcopy(before)
    if qty <= 0:
        data["rows"] = [r for r in data["rows"] if not _match(r, part, colour)]
    elif before:
        first = next(r for r in data["rows"] if _match(r, part, colour))
        first["qty"] = qty
        data["rows"] = [r for r in data["rows"] if not _match(r, part, colour) or r is first]
    else:
        row = {"part": part, "colour": colour, "qty": qty, "rb_part": part, "rb_colour": 0}
        data["rows"].append({**row, **(template or {})})
    return kept


def _apply(data: dict, op: Any) -> dict | None:
    """Applies one op to the in-memory source; returns its log entry (with what undo needs) or None for a no-op."""
    if not isinstance(op, dict):
        raise InventoryError(400, "Each change must be an object")
    kind = op.get("op")
    if kind in ("add", "set", "remove"):
        part, colour = _clean_part(op.get("part")), _clean_colour(op.get("colour"))
        before = _qty_of(data, part, colour)
        if kind == "add":
            delta = _clean_qty(op.get("qty"), 1)
            after = before + delta
            if after > MAX_QTY:
                raise InventoryError(400, f"A line cannot hold more than {MAX_QTY:,} pieces")
        elif kind == "set":
            after = _clean_qty(op.get("qty"), 0)
        else:
            after = 0
        if before == 0 and after > 0:
            _check_known_part(part)
        if after == before:
            return None
        rows_before = _set_qty(data, part, colour, after)
        return {"op": kind, "part": part, "colour": colour, "before": before, "after": after, "rows_before": rows_before}
    if kind in ("review_resolve", "review_discard"):
        review = data.setdefault("review", [])
        index = next((i for i, r in enumerate(review) if r.get("id") == op.get("id")), None)
        if index is None:
            raise InventoryError(404, f"No colour-family piece {str(op.get('id'))[:20]!r} in this source")
        item = review[index]
        if kind == "review_discard":
            del review[index]
            return {"op": kind, "review_before": copy.deepcopy(item), "index": index, "part": item["part"], "qty": item["qty"]}
        colour = _clean_colour(op.get("colour"))
        qty = item["qty"] if op.get("qty") is None else _clean_qty(op.get("qty"), 1)
        if qty > item["qty"]:
            raise InventoryError(400, f"Only {item['qty']} of these pieces are waiting for a colour")
        part = part_key(item["part"])
        before = _qty_of(data, part, colour)
        entry = {"op": kind, "review_before": copy.deepcopy(item), "index": index, "part": part, "colour": colour, "qty": qty,
                 "before": before, "after": before + qty, "rows_before": copy.deepcopy([r for r in data["rows"] if _match(r, part, colour)])}
        _set_qty(data, part, colour, before + qty, {"rb_part": str(item.get("brickit_part") or part)})
        if qty == item["qty"]:
            del review[index]
        else:
            item["qty"] -= qty
        if not review:
            data.pop("review", None)
        return entry
    raise InventoryError(400, f"Unknown change {str(kind)[:30]!r}")


def apply_ops(source_id: str, ops: Any, note: str = "") -> dict:
    """Applies a list of changes to one source as one undoable batch.

    ops: {"op": "add", part, colour, qty} | {"op": "set", part, colour, qty} | {"op": "remove", part, colour} |
    {"op": "review_resolve", id, colour, qty?} | {"op": "review_discard", id}. Everything is checked before anything is
    written, so a bad line changes nothing. Returns {"batch", "changes", "source"} (batch is None for a no-op).
    """
    if not isinstance(ops, list) or not ops:
        raise InventoryError(400, "Send at least one change")
    if len(ops) > MAX_OPS:
        raise InventoryError(400, f"At most {MAX_OPS} changes at a time")
    note = note.strip()[:MAX_NOTE] if isinstance(note, str) else ""
    with store.lock:
        data = _editable(source_id, create=source_id == LOOSE_ID)
        entries = [e for e in (_apply(data, op) for op in ops) if e is not None]
        if not entries:
            return {"batch": None, "changes": 0, "source": inventories.summary(data)}
        existing = _read_log(source_id)
        batch = _next_batch(existing)
        _save(data)
        _append_log(source_id, _stamp(entries, batch, note))
    return {"batch": batch, "changes": len(entries), "source": inventories.summary(data)}


# --- history and undo --------------------------------------------------------

def _entry_text(e: dict) -> str:
    name = lambda: f"{e.get('part', '')} {part_info.colour_name(e['colour'])}" if "colour" in e else e.get("part", "")  # noqa: E731
    op = e["op"]
    if op == "add":
        return f"Added {e['after'] - e['before']} x {name()}"
    if op in ("set", "remove"):
        return f"Removed {name()}" if e["after"] == 0 else f"{name()}: {e['before']} to {e['after']}"
    if op == "review_resolve":
        return f"Gave {e['qty']} x {e['part']} the colour {part_info.colour_name(e['colour'])}"
    if op == "review_discard":
        return f"Discarded {e['qty']} x {e['part']} (not mine)"
    if op == "replace":
        d = e.get("diff", {})
        return f"Replaced by a new import (+{d.get('added', 0)} -{d.get('removed', 0)} ~{d.get('changed', 0)})"
    if op == "undo":
        return "Undid a change"
    return "Created"


def _batches(entries: list[dict]) -> list[dict]:
    by: OrderedDict[int, list[dict]] = OrderedDict()
    for e in entries:
        by.setdefault(e["batch"], []).append(e)
    undone = {e["target"] for e in entries if e["op"] == "undo"}
    out = []
    for batch, items in by.items():
        kinds = {e["op"] for e in items}
        text = "; ".join(_entry_text(e) for e in items[:3]) + (f" and {len(items) - 3} more" if len(items) > 3 else "")
        out.append({"batch": batch, "ts": items[0]["ts"], "note": items[0].get("note", ""), "summary": text, "changes": len(items),
                    "kind": "undo" if kinds == {"undo"} else "create" if kinds == {"create"} else "replace" if "replace" in kinds else "edit",
                    "undone": batch in undone,
                    "undoable": batch not in undone and not kinds <= {"undo", "create"}})
    return out


def history(source_id: str, limit: int = 30) -> dict:
    if not store.exists(source_id) and source_id != LOOSE_ID:
        raise InventoryError(404, "no such inventory")
    batches = _batches(_read_log(source_id))
    undoable = [b["batch"] for b in batches if b["undoable"]]
    return {"source": source_id, "batches": list(reversed(batches))[:max(1, min(limit, 100))], "undo_batch": undoable[-1] if undoable else None}


def _restore_review(data: dict, item: dict, index: int) -> None:
    review = data.setdefault("review", [])
    for i, r in enumerate(review):
        if r.get("id") == item["id"]:
            review[i] = copy.deepcopy(item)
            return
    review.insert(min(index, len(review)), copy.deepcopy(item))


def _reverse(data: dict, e: dict) -> None:
    op = e["op"]
    if op in ("add", "set", "remove", "review_resolve"):
        _put_rows(data, e["part"], e["colour"], e["rows_before"])
    if op in ("review_resolve", "review_discard"):
        _restore_review(data, e["review_before"], e["index"])
    if op == "replace":
        data["rows"] = copy.deepcopy(e["rows_before"])
        for key, value in e["meta_before"].items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = copy.deepcopy(value)


def undo(source_id: str) -> dict:
    """Reverses the last batch that is neither undone nor an undo; the log keeps both, so history stays honest."""
    with store.lock:
        entries = _read_log(source_id)
        undoable = [b for b in _batches(entries) if b["undoable"]]
        if not undoable:
            raise InventoryError(409, "Nothing to undo")
        target = undoable[-1]["batch"]
        data = _editable(source_id)
        batch_entries = [e for e in entries if e["batch"] == target]
        for e in reversed(batch_entries):
            _reverse(data, e)
        for e in batch_entries:
            if e["op"] == "replace" and e.get("csv_before") is not None:
                store.write_csv(source_id, e["csv_before"].encode())
        if not data.get("review"):
            data.pop("review", None)
        _save(data)
        _append_log(source_id, _stamp([{"op": "undo", "target": target}], _next_batch(entries)))
    return {"undone": target, "source": inventories.summary(data), "summary": undoable[-1]["summary"]}


# --- import: preview, diff, commit -------------------------------------------

_previews: OrderedDict[str, dict] = OrderedDict()
_previews_lock = threading.Lock()


def _rows_by_key(rows: list[dict]) -> dict[tuple[str, int], int]:
    out: dict[tuple[str, int], int] = {}
    for r in rows:
        key = (part_key(r["part"]), r["colour"])
        out[key] = out.get(key, 0) + r["qty"]
    return out


def diff_rows(old_rows: list[dict], new_rows: list[dict]) -> dict:
    """What replacing one list of rows by another changes: added, removed and changed (part, colour) lines."""
    old, new = _rows_by_key(old_rows), _rows_by_key(new_rows)

    def line(key, before, after):
        return {"part": key[0], "colour": key[1], "colour_name": part_info.colour_name(key[1]),
                "description": part_info.describe(key[0]), "before": before, "after": after}
    added = [line(k, 0, q) for k, q in sorted(new.items()) if k not in old]
    removed = [line(k, q, 0) for k, q in sorted(old.items()) if k not in new]
    changed = [line(k, old[k], q) for k, q in sorted(new.items()) if k in old and old[k] != q]
    return {"added": len(added), "removed": len(removed), "changed": len(changed),
            "unchanged": sum(1 for k, q in new.items() if old.get(k) == q),
            "pieces_before": sum(old.values()), "pieces_after": sum(new.values()),
            "lines": {"added": added[:MAX_DIFF_LINES], "removed": removed[:MAX_DIFF_LINES], "changed": changed[:MAX_DIFF_LINES]}}


def _import_totals(data: dict) -> dict:
    rows, review = data.get("rows", []), data.get("review", [])
    return {"lines": len(rows), "pieces": sum(r["qty"] for r in rows), "parts": len({r["part"] for r in rows}),
            "colours": len({r["colour"] for r in rows}), "review_items": len(review), "review_pieces": sum(r["qty"] for r in review),
            "unmapped_pieces": sum(u["qty"] for u in data.get("unmapped_parts", [])) + sum(u["qty"] for u in data.get("unmapped_colours", []))}


def _existing_match(name: str) -> str | None:
    slug = inventories.slugify(name)
    for item in inventories.list_inventories():
        if item["kind"] == "import" and slug in (item["id"], inventories.slugify(item["name"])):
            return item["id"]
    return None


async def preview_import(name: str, csv_text: str, include_spares: bool = False, family_colours: dict | None = None,
                         filename: str = "", import_format: str = "", mapping: dict | None = None) -> dict:
    """Parses an upload and keeps the result for `commit_import`; nothing is stored yet.

    `import_format` overrides the detected format. A CSV nobody recognises answers `{"needs_mapping": {...}}` (headers, sample
    rows, a guess, and the mapping the owner confirmed for the same headers before, applied here without asking again);
    send the file again with `mapping` to read it."""
    remembered = False
    try:
        parsed = await inventories.parse_csv(name, csv_text, include_spares, family_colours, import_format=import_format, mapping=mapping)
    except inventories.NeedsMapping as needed:
        saved = store.read_import_mappings().get(needed.info.get("signature", ""))
        parsed = None
        if saved and not mapping:
            try:
                parsed = await inventories.parse_csv(name, csv_text, include_spares, family_colours, import_format="generic-csv", mapping=saved)
                remembered = True
            except InventoryError:
                parsed = None                           # the saved columns no longer fit this file: ask again
        if parsed is None:
            return {"needs_mapping": {**needed.info, "saved": saved or None, "systems": list(inventories.COLOUR_SYSTEMS)}}
    fmt = parsed["report"].get("format", "")
    token = secrets.token_urlsafe(9)
    with _previews_lock:
        now = time.time()
        for key in [k for k, p in _previews.items() if now - p["created"] > PREVIEW_TTL]:
            del _previews[key]
        _previews[token] = {"parsed": parsed, "created": now, "name": name.strip(), "filename": filename, "format": fmt}
        while len(_previews) > MAX_PREVIEWS:
            _previews.popitem(last=False)
    return {"preview_id": token, "name": name.strip(), "format": fmt, "report": parsed["report"],
            "totals": _import_totals(parsed["data"]), "same_name": _existing_match(name),
            **({"mapping": parsed["report"]["mapping"], "mapping_remembered": remembered} if parsed["report"].get("mapping") else {})}


def _preview(token: str) -> dict:
    with _previews_lock:
        found = _previews.get(token)
    if found is None:
        raise InventoryError(410, "This preview has expired. Choose the file again")
    return found


def _replaceable(source_id: str) -> dict:
    data = store.load(source_id)
    if (data.get("kind") or "import") != "import":
        raise InventoryError(400, "Only an imported parts list can be replaced by a new file")
    return data


def preview_diff(token: str, replace: str) -> dict:
    """What committing the preview over an existing source would change."""
    pv = _preview(token)
    old = _replaceable(replace)
    new = pv["parsed"]["data"]
    d = diff_rows(old.get("rows", []), new.get("rows", []))
    old_review, new_review = old.get("review", []), new.get("review", [])
    d["review"] = {"before_items": len(old_review), "after_items": len(new_review),
                   "before_pieces": sum(r["qty"] for r in old_review), "after_pieces": sum(r["qty"] for r in new_review)}
    d["target"] = {"id": replace, "name": old.get("name", replace)}
    return d


def _store_new(data: dict, raw: bytes, name: str) -> dict:
    with store.lock:
        if inventories._name_taken(name):
            raise InventoryError(409, f"An inventory called {name!r} already exists")
        sid = data["id"]
        if sid in inventories.RESERVED_IDS or store.exists(sid):
            raise InventoryError(409, f"An inventory called {name!r} already exists")
        data["created_at"] = time.time()
        store.write_csv(sid, raw)
        _save(data)
        _append_log(sid, _stamp([{"op": "create", "what": "import"}], 1, f"Imported {data.get('source_filename') or name}"))
    return data


def replace_source(source_id: str, new: dict, raw: bytes, name: str | None = None) -> tuple[dict, dict, int | None]:
    """Replaces an imported source by a re-import, keeping its id (chats that use it keep working). One undoable batch.

    Returns (new data, diff, batch)."""
    with store.lock:
        old = copy.deepcopy(_replaceable(source_id))
        data = copy.deepcopy(new)
        data["id"], data["created_at"] = source_id, old.get("created_at", time.time())
        data["name"] = name or old.get("name", source_id)
        data["source_filename"] = f"{source_id}.csv"
        notes = {(part_key(r["part"]), r["colour"]): r["note"] for r in old.get("rows", []) if r.get("note")}
        for r in data["rows"]:
            if (part_key(r["part"]), r["colour"]) in notes:
                r["note"] = notes[(part_key(r["part"]), r["colour"])]
        d = diff_rows(old.get("rows", []), data["rows"])
        meta_keys = ("review", "unmapped_parts", "unmapped_colours", "warnings", "import", "origin", "name", "source_filename")
        csv_before = store.read_csv(source_id, 2 * 1024 * 1024)
        entry = {"op": "replace", "rows_before": old.get("rows", []), "meta_before": {k: old.get(k) for k in meta_keys},
                 "csv_before": csv_before, "diff": {k: d[k] for k in ("added", "removed", "changed", "pieces_before", "pieces_after")}}
        if "kind" not in data and old.get("kind"):
            data["kind"] = old["kind"]
        batch = _next_batch(_read_log(source_id))
        store.write_csv(source_id, raw)
        _save(data)
        _append_log(source_id, _stamp([entry], batch, "Re-import"))
    return data, d, batch


def commit_import(token: str, name: str | None = None, replace: str | None = None) -> dict:
    """Stores a previewed import: as a new source, or over `replace` keeping that source's id."""
    pv = _preview(token)
    parsed = pv["parsed"]
    data, raw, report = copy.deepcopy(parsed["data"]), parsed["raw"], parsed["report"]
    if replace:
        data, diff, batch = replace_source(replace, data, raw, name=inventories.clean_name(name) if name else None)
        result = {"inventory": inventories.summary(data), "report": report, "diff": {k: v for k, v in diff.items() if k != "lines"}, "batch": batch}
    else:
        final = inventories.clean_name(name or pv["name"])
        data["name"], data["id"] = final, inventories.slugify(final)
        data["source_filename"] = f"{data['id']}.csv"
        if data["id"] in inventories.RESERVED_IDS:
            raise InventoryError(400, f"The name {final!r} is reserved; choose another one")
        _store_new(data, raw, final)
        result = {"inventory": inventories.summary(data), "report": report}
    if report.get("mapping") and report.get("header_signature"):
        store.write_import_mapping(report["header_signature"], report["mapping"])      # next file with these headers needs no questions
    with _previews_lock:
        _previews.pop(token, None)
    return result


async def import_csv(name: str, csv_text: str, include_spares: bool = False, family_colours: dict | None = None, replace: str = "") -> dict:
    """The one-step import the original API offers (preview and commit in one go); `replace` maps families again."""
    name = inventories.clean_name(name)
    if not replace:
        if name and inventories.slugify(name) in inventories.RESERVED_IDS:
            raise InventoryError(400, f"The name {name!r} is reserved; choose another one")
        if inventories._name_taken(name):
            raise InventoryError(409, f"An inventory called {name!r} already exists")
    try:
        parsed = await inventories.parse_csv(name, csv_text, include_spares, family_colours, replace=replace)
    except inventories.NeedsMapping as needed:
        raise InventoryError(400, str(needed)) from None         # the one-step API cannot ask which columns to use
    if replace:
        data, _, _ = replace_source(replace, parsed["data"], parsed["raw"], name=name)
    else:
        data = _store_new(parsed["data"], parsed["raw"], name)
    return {"inventory": inventories.summary(data), "report": parsed["report"]}


# --- sets --------------------------------------------------------------------

DEFAULT_BRAND = "LEGO"
MAX_BRAND_CHARS = 40


def _clean_brand(brand: Any) -> str:
    text = " ".join(str(brand or "").split())
    if len(text) > MAX_BRAND_CHARS:
        raise InventoryError(400, f"Brand is at most {MAX_BRAND_CHARS} characters")
    return text or DEFAULT_BRAND


def set_record(data: dict) -> dict | None:
    """The set block of a source with every field of a set record filled in, or None for a source that is not a set.
    Sources stored before brands and images existed read as brand LEGO with no image, so they need no rewrite."""
    block = data.get("set")
    if data.get("kind") != "set" or not isinstance(block, dict):
        return None
    image = block.get("image")
    return {**block, "brand": block.get("brand") or DEFAULT_BRAND, "image": image if isinstance(image, str) and image else None,
            "copies": block.get("copies") or 1, "state": block.get("state") or "parts"}


def add_set(set_num: str, copies: int = 1, state: str = "parts", brand: str = DEFAULT_BRAND) -> dict:
    """Adds a catalogue set as a source of kind "set": its parts list copied in (so a later catalogue refresh cannot change
    what the owner owns), multiplied by `copies`. `state` "built" keeps the parts counted as owned and records that the set is
    assembled (what a later reservation will use)."""
    import set_catalog
    if state not in ("parts", "built"):
        raise InventoryError(400, "state must be parts or built")
    brand = _clean_brand(brand)
    copies = _clean_qty(copies, 1, "number of copies")
    if copies > 99:
        raise InventoryError(400, "At most 99 copies of a set")
    try:
        catalog = set_catalog.load()
    except set_catalog.CatalogError as exc:
        raise InventoryError(503, str(exc)) from None
    info = catalog.get(str(set_num))
    if info is None:
        raise InventoryError(404, f"No set {str(set_num)[:30]!r} in the catalogue")
    lines = catalog.lines(info["set_num"])
    dropped = info["dropped_part"] + info["dropped_colour"]
    title = f"{info['set_num']} {info['name']}"[:inventories.MAX_NAME_CHARS]
    with store.lock:
        n = 1
        base_id = inventories.slugify(f"set-{info['set_num']}")
        sid, name = base_id, title
        while store.exists(sid) or inventories._name_taken(name):
            n += 1
            sid, name = f"{base_id}-{n}"[:60], f"{title[:inventories.MAX_NAME_CHARS - 6]} ({n})"
        data = {"id": sid, "name": name, "created_at": time.time(), "source_filename": None, "kind": "set",
                "set": {"set_num": info["set_num"], "brand": brand, "name": info["name"], "year": info["year"], "theme": info["theme"],
                        "image": None, "num_parts": info["num_parts"], "copies": copies, "state": state, "dropped_pieces": dropped * copies},
                "origin": {"format": "set-catalog", "catalog": {k: v for k, v in catalog.meta.items() if isinstance(v, (str, int, float))}},
                "rows": [{"part": p, "colour": c, "qty": q * copies, "rb_part": p, "rb_colour": 0} for p, c, q in lines],
                "unmapped_parts": [], "unmapped_colours": [],
                "warnings": [f"{dropped * copies} piece(s) of this set have no LDraw equivalent and are not listed"] if dropped else []}
        _save(data)
        _append_log(sid, _stamp([{"op": "create", "what": "set"}], 1, f"Added set {info['set_num']}"))
    return {"inventory": inventories.summary(data), "set": set_record(data)}


def owned_sets() -> list[dict]:
    """Every owned set as a record: brand, image, copies, state and piece counts. `reserved` is the set's pieces while it
    is built; `missing` is how many of those the collection no longer covers (other reservations took them);
    `on_hand` is the rest."""
    import reservations
    owned, _ = store.owned_totals()
    reserved = reservations.reserved_totals()
    found = []
    for sid in store.source_ids():
        try:
            data = store.load(sid)
        except InventoryError:
            continue
        record = set_record(data)
        if record is None:
            continue
        rows = [r for r in data.get("rows", []) if int(r["qty"]) > 0]
        pieces = sum(int(r["qty"]) for r in rows)
        built = record["state"] == "built"
        missing = 0
        if built:
            for r in rows:
                key = (reservations.part_key(r["part"]), int(r["colour"]))
                missing += min(int(r["qty"]), max(0, reserved.get(key, 0) - owned.get(key, 0)))
        found.append({**record, "id": sid, "source_name": data.get("name", sid), "pieces": pieces,
                      "distinct_parts": len({r["part"] for r in rows}), "reserved": pieces if built else 0,
                      "missing": missing, "on_hand": pieces - missing})
    return found


# --- notes -------------------------------------------------------------------

def get_notes() -> dict[str, str]:
    return store.read_notes()


def set_note(part: str, text: Any) -> dict:
    """The free-text note of a part ("blue tub"); empty removes it. Later convertible into a storage location."""
    part = _clean_part(part)
    if not isinstance(text, str):
        raise InventoryError(400, "The note must be text")
    text = text.strip()
    if len(text) > MAX_NOTE:
        raise InventoryError(400, f"The note is longer than {MAX_NOTE} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise InventoryError(400, "The note contains control characters")
    with _notes_lock:
        notes = get_notes()
        before = notes.get(part, "")
        if before == text:
            return {"part": part, "note": text}
        if text:
            notes[part] = text
        else:
            notes.pop(part, None)
        store.write_notes(notes, {"ts": round(time.time(), 3), "part": part, "before": before, "after": text})
    return {"part": part, "note": text}


# --- quick add commit --------------------------------------------------------

def quick_add(items: Any, source: str = LOOSE_ID, note: str = "Quick add") -> dict:
    """Adds resolved lines [{part, colour, qty}] to a source (Loose parts by default) as one batch."""
    if not isinstance(items, list) or not items:
        raise InventoryError(400, "Nothing to add")
    ops = []
    for it in items:
        if not isinstance(it, dict):
            raise InventoryError(400, "Each line needs a part, a colour and a quantity")
        ops.append({"op": "add", "part": it.get("part"), "colour": it.get("colour"), "qty": it.get("qty")})
    result = apply_ops(source, ops, note)
    return {**result, "pieces": sum(o["qty"] for o in ops)}


# --- browsing ----------------------------------------------------------------

def _source_rows(source_ids: list[str] | None = None) -> list[tuple[dict, dict]]:
    out = []
    for sid in store.source_ids():
        if source_ids is not None and sid not in source_ids:
            continue
        try:
            data = store.load(sid)
        except InventoryError:
            continue
        out.append((data, inventories.summary(data)))
    return out


def _tokens(query: str) -> tuple[list[str], list[str]]:
    """(plain words, sizes) of a search: "2x4 red brick" -> (["red", "brick"], ["2x4"])."""
    sizes = []

    def take(m):
        sizes.append(part_info.size_from_match(m))
        return " "
    rest = part_info.SIZE_RE.sub(take, query.casefold())
    return [w.replace("grey", "gray") for w in rest.split()], sizes


def _natural(part: str) -> tuple[int, str]:
    m = re.match(r"\d+", part)
    return (int(m.group()) if m else 10 ** 12, part)


def browse(query: str = "", colours: list[int] | None = None, category: str = "", size: str = "", source_ids: list[str] | None = None,
           sort: str = "qty", limit: int = 100, offset: int = 0, maybe_only: bool = False) -> dict:
    """The collection grouped by part: filters, search, counts and facets. Exact rows only; "maybe" pieces ride along apart."""
    names = part_info.colour_names()
    name_of = lambda c: names.get(c, f"Colour {c}")  # noqa: E731
    words, sizes = _tokens(query)
    if size:
        sizes.append(size.casefold())
    parts: dict[str, dict] = {}
    all_sources = _source_rows()
    chosen = [(d, s) for d, s in all_sources if source_ids is None or d["id"] in source_ids]
    for data, info in chosen:
        for r in data.get("rows", []):
            if r["qty"] <= 0:
                continue
            p = parts.setdefault(part_key(r["part"]), {"colours": {}, "sources": {}, "maybe": 0, "maybe_items": 0})
            p["colours"][r["colour"]] = p["colours"].get(r["colour"], 0) + r["qty"]
            p["sources"][info["id"]] = p["sources"].get(info["id"], 0) + r["qty"]
        for item in data.get("review", []):
            p = parts.setdefault(part_key(item["part"]), {"colours": {}, "sources": {}, "maybe": 0, "maybe_items": 0})
            p["maybe"] += item["qty"]
            p["maybe_items"] += 1
    notes = get_notes()
    source_names = {s["id"]: s["name"] for _, s in all_sources}
    facet_colours: dict[int, int] = {}
    facet_cats: dict[str, int] = {}
    facet_sizes: dict[str, int] = {}
    for part, p in parts.items():
        desc = part_info.describe(part)
        for c, q in p["colours"].items():
            facet_colours[c] = facet_colours.get(c, 0) + q
        if p["colours"]:
            facet_cats[part_info.category(part) or "Other"] = facet_cats.get(part_info.category(part) or "Other", 0) + 1
            sz = part_info.size_of(desc)
            if sz:
                facet_sizes[sz] = facet_sizes.get(sz, 0) + 1
    rows = []
    for part, p in parts.items():
        desc, cat = part_info.describe(part), part_info.category(part)
        sz = part_info.size_of(desc)
        shown = dict(p["colours"])
        if category and cat.casefold() != category.casefold():
            continue
        if sizes and any(s != sz for s in sizes):
            continue
        if colours:
            shown = {c: q for c, q in shown.items() if c in colours}
        text = f"{part} {desc} {cat} {notes.get(part, '')}".casefold()
        ok = True
        for w in words:
            if w in text:
                continue
            hit = {c for c in shown if w in name_of(c).casefold().replace("grey", "gray")}
            if not hit:
                ok = False
                break
            shown = {c: q for c, q in shown.items() if c in hit}
        if not ok or (not shown and not (p["maybe"] and not colours)):
            continue
        if maybe_only and not p["maybe"]:
            continue
        total = sum(shown.values())
        rows.append({"part": part, "description": desc, "category": cat, "size": sz, "total": total, "note": notes.get(part, ""),
                     "colours": [{"colour": c, "name": name_of(c), "qty": q} for c, q in sorted(shown.items(), key=lambda kv: (-kv[1], kv[0]))],
                     "sources": [{"id": s, "name": source_names.get(s, s), "qty": q} for s, q in sorted(p["sources"].items(), key=lambda kv: -kv[1])],
                     "maybe": p["maybe"]})
    key = {"part": lambda r: _natural(r["part"]), "name": lambda r: (r["description"].casefold(), r["part"]),
           "colours": lambda r: (-len(r["colours"]), r["part"])}.get(sort, lambda r: (-r["total"], r["part"]))
    rows.sort(key=key)
    review_items = sum(len(d.get("review", [])) for d, _ in chosen)
    return {
        "parts": rows[offset:offset + limit], "total": len(rows),
        "totals": {"pieces": sum(r["total"] for r in rows), "parts": sum(1 for r in rows if r["total"] > 0), "review_items": review_items,
                   "review_pieces": sum(r["qty"] for d, _ in chosen for r in d.get("review", []))},
        "collection": {"pieces": sum(facet_colours.values()), "parts": len([1 for p in parts.values() if p["colours"]]),
                       "colours": len(facet_colours), "sources": len(all_sources)},
        "facets": {
            "colours": [{"colour": c, "name": name_of(c), "qty": q} for c, q in sorted(facet_colours.items(), key=lambda kv: (-kv[1], kv[0]))],
            "categories": [{"category": c, "parts": n} for c, n in sorted(facet_cats.items(), key=lambda kv: (-kv[1], kv[0]))],
            "sizes": [{"size": s, "parts": n} for s, n in sorted(facet_sizes.items(), key=lambda kv: (-kv[1], kv[0]))[:60]],
            "sources": [{"id": s["id"], "name": s["name"], "kind": s["kind"], "pieces": s["total_pieces"]} for _, s in all_sources],
        },
    }


def part_detail(part: str) -> dict:
    part = _clean_part(part)
    rows, maybe = [], []
    for data, info in _source_rows():
        for r in data.get("rows", []):
            if part_key(r["part"]) == part and r["qty"] > 0:
                rows.append({"source": info["id"], "source_name": info["name"], "kind": info["kind"], "colour": r["colour"],
                             "colour_name": part_info.colour_name(r["colour"]), "qty": r["qty"], **({"note": r["note"]} if r.get("note") else {})})
        for item in data.get("review", []):
            if part_key(item["part"]) == part:
                maybe.append({"source": info["id"], "source_name": info["name"], "id": item["id"], "family": item.get("family", ""), "qty": item["qty"]})
    desc = part_info.describe(part)
    return {"part": part, "description": desc, "category": part_info.category(part), "size": part_info.size_of(desc),
            "note": get_notes().get(part, ""), "total": sum(r["qty"] for r in rows), "rows": rows, "maybe": maybe}


# --- review queue ------------------------------------------------------------

def review_queue() -> dict:
    """Colour-family pieces waiting for an exact colour, grouped by source and family. Never counted until resolved."""
    groups = []
    owned: dict[str, set[int]] = {}
    sources_ = _source_rows()
    for data, _ in sources_:
        for r in data.get("rows", []):
            owned.setdefault(part_key(r["part"]), set()).add(r["colour"])
    for data, info in sources_:
        by_family: dict[str, list[dict]] = {}
        for item in data.get("review", []):
            candidates = [c for c in item.get("candidates", []) if isinstance(c, int)]
            by_family.setdefault(item.get("family", ""), []).append({
                "id": item["id"], "part": item["part"], "description": part_info.describe(item["part"]), "qty": item["qty"],
                "note": item.get("note", ""), "brickit_part": item.get("brickit_part", ""),
                "candidates": [{"colour": c, "name": part_info.colour_name(c), "owned": c in owned.get(part_key(item["part"]), ())} for c in candidates]})
        for family, items in sorted(by_family.items()):
            first = items[0]["candidates"]
            groups.append({"source": info["id"], "source_name": info["name"], "family": family, "pieces": sum(i["qty"] for i in items),
                           "candidates": first, "items": items})
    return {"groups": groups, "items": sum(len(g["items"]) for g in groups), "pieces": sum(g["pieces"] for g in groups)}


def resolve_family(source_id: str, family: str, colour: int) -> dict:
    """Gives every waiting piece of one colour family the same exact colour (one undoable batch)."""
    data = store.load(source_id)
    ids = [r["id"] for r in data.get("review", []) if r.get("family") == family]
    if not ids:
        raise InventoryError(404, f"Nothing from the family {family[:30]!r} is waiting in this source")
    return apply_ops(source_id, [{"op": "review_resolve", "id": i, "colour": colour} for i in ids], f"All {family} to {part_info.colour_name(colour)}")
