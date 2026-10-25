"""Reservations and shortage lists: which parts are held by models the owner built, and what a model is still missing.

Available = owned - reserved, where reserved is the parts of every owned set that is marked built plus every active
reservation (`I built this` on a model). Reserving never changes a source: releasing deletes the reservation, taking a
set apart flips its `state`. A reservation may exceed what is owned (parts since lost); that shows as "short by N".
Only exact `rows` are counted, so a "maybe" (colour-family) piece never inflates an answer; the shortage list reports
those separately as `maybe`.

Storage goes through collection_store, like the rest of the collection feature.
"""
from __future__ import annotations

import csv
import io
import secrets
import time
from typing import Any

import collection_store as store
import inventories
import part_info
from inventories import InventoryError

MAX_ROWS = 20_000
Key = tuple[str, int]


def part_key(part: Any) -> str:
    return str(part).strip().casefold().removesuffix(".dat")


def _built_sets() -> list[dict]:
    """Sources of kind "set" whose state is "built": their parts are inside an assembled set."""
    found = []
    for sid in store.source_ids():
        try:
            data = store.load(sid)
        except InventoryError:
            continue
        if data.get("kind") == "set" and (data.get("set") or {}).get("state") == "built":
            found.append(data)
    return found


def _add_rows(into: dict[Key, int], rows) -> None:
    for part, colour, qty in rows:
        if int(qty) > 0:
            key = (part_key(part), int(colour))
            into[key] = into.get(key, 0) + int(qty)


def reserved_totals(exclude_build: str | None = None) -> dict[Key, int]:
    """Parts held back by built sets and reservations; `exclude_build` leaves out one build's own reservations
    (the editor judges a draft against what the OTHER models leave free)."""
    reserved: dict[Key, int] = {}
    for data in _built_sets():
        _add_rows(reserved, ((r["part"], r["colour"], r["qty"]) for r in data.get("rows", [])))
    for res in store.read_reservations():
        if exclude_build is None or res.get("build") != exclude_build:
            try:
                _add_rows(reserved, res["rows"])
            except (TypeError, ValueError):
                continue
    return reserved


def available_totals(exclude_build: str | None = None) -> tuple[dict[Key, int], str]:
    """(owned - reserved, never below 0; a signature that changes when a source or a reservation does)."""
    owned, signature = store.owned_totals()
    reserved = reserved_totals(exclude_build)
    available = {key: max(0, qty - reserved.get(key, 0)) for key, qty in owned.items()}
    return available, f"{signature}|res:{store.reservations_signature()}:{exclude_build or ''}"


# --- reservations -------------------------------------------------------------

def _clean_rows(usage: dict[Key, int]) -> list[list]:
    rows = [[part_key(p), int(c), int(q)] for (p, c), q in sorted(usage.items()) if int(q) > 0]
    if not rows:
        raise InventoryError(400, "This model has no parts to reserve")
    if len(rows) > MAX_ROWS:
        raise InventoryError(400, "Too many different parts to reserve")
    return rows


def reserve(model: str, usage: dict[Key, int], build: str = "", chat: str | None = None) -> dict:
    """One reservation per model file: reserving again replaces it (the model changed since)."""
    rows = _clean_rows(usage)
    with store.lock:
        kept = [r for r in store.read_reservations() if r.get("model") != model]
        record = {"id": "res-" + secrets.token_hex(4), "model": model, "build": build, "chat": chat, "state": "built",
                  "created_at": round(time.time(), 3), "source": "bom", "rows": rows}
        store.write_reservations([*kept, record])
    return record


def release(reservation_id: str) -> dict:
    with store.lock:
        current = store.read_reservations()
        gone = [r for r in current if r.get("id") == reservation_id]
        if not gone:
            raise InventoryError(404, "no such reservation")
        store.write_reservations([r for r in current if r.get("id") != reservation_id])
    return gone[0]


def release_model(model: str) -> int:
    """Drops the reservation of a deleted model file; returns how many were released."""
    with store.lock:
        current = store.read_reservations()
        kept = [r for r in current if r.get("model") != model]
        if len(kept) != len(current):
            store.write_reservations(kept)
        return len(current) - len(kept)


def set_state(source_id: str, state: str) -> dict:
    """Marks an owned set as assembled ("built": its parts are reserved) or taken apart ("parts": free again)."""
    if state not in ("parts", "built"):
        raise InventoryError(400, "state must be parts or built")
    with store.lock:
        data = store.load_copy(source_id)
        if data.get("kind") != "set" or not isinstance(data.get("set"), dict):
            raise InventoryError(400, "Only a set can be built or taken apart")
        data["set"]["state"] = state
        store.save(data)
    return {"inventory": inventories.summary(data), "set": data["set"]}


def listing() -> dict:
    """Active reservations and built sets, each with what it holds beyond what is owned ("short by N")."""
    owned, _ = store.owned_totals()
    built = [{"source": d["id"], "name": d.get("name", d["id"]), "pieces": sum(int(r["qty"]) for r in d.get("rows", []))}
             for d in _built_sets()]
    items = []
    for res in sorted(store.read_reservations(), key=lambda r: r.get("created_at", 0)):
        short = []
        for part, colour, qty in res["rows"]:
            have = owned.get((part_key(part), int(colour)), 0)
            if int(qty) > have:
                short.append({"part": part, "colour": int(colour), "colour_name": part_info.colour_name(int(colour)),
                              "reserved": int(qty), "owned": have, "short": int(qty) - have})
        items.append({**{k: v for k, v in res.items() if k != "rows"}, "pieces": sum(int(r[2]) for r in res["rows"]),
                      "distinct_parts": len({r[0] for r in res["rows"]}), "short_pieces": sum(s["short"] for s in short),
                      "short": short[:50]})
    return {"reservations": items, "built_sets": built}


# --- shortage -----------------------------------------------------------------

def _maybe() -> dict[Key, int]:
    """Pieces waiting in the review queue, counted once per candidate colour: possible, never exact."""
    maybe: dict[Key, int] = {}
    for sid in store.source_ids():
        try:
            review = store.load(sid).get("review") or []
        except InventoryError:
            continue
        for item in review:
            for colour in item.get("candidates") or []:
                key = (part_key(item.get("part", "")), int(colour))
                maybe[key] = maybe.get(key, 0) + int(item.get("qty", 0))
    return maybe


def shortage(usage: dict[Key, int], mode: str = "exact", exclude_build: str | None = None) -> dict:
    """What a model needs that the collection leaves free: lines {part, colour, needed, owned, reserved, available, missing,
    substitutes, maybe}. `substitute` also reports how much of the gap other owned colours of the same part could fill
    (`missing_with_substitutes`); the exact `missing` is never reduced."""
    if mode not in ("exact", "substitute"):
        raise InventoryError(400, "mode must be exact or substitute")
    owned, _ = store.owned_totals()
    reserved = reserved_totals(exclude_build)
    maybe = _maybe()
    free_by_part: dict[str, list[tuple[int, int]]] = {}
    for (part, colour), qty in owned.items():
        free = qty - reserved.get((part, colour), 0)
        if free > 0:
            free_by_part.setdefault(part, []).append((colour, free))
    lines, needed_total, covered = [], 0, 0
    for (part, colour), need in sorted(usage.items()):
        part = part_key(part)
        key = (part, colour)
        have, held = owned.get(key, 0), reserved.get(key, 0)
        available = max(0, have - held)
        needed_total += need
        covered += min(need, available)
        if need <= available:
            continue
        missing = need - available
        others = sorted(((c, q) for c, q in free_by_part.get(part, []) if c != colour), key=lambda cq: (-cq[1], cq[0]))
        line = {"part": part, "colour": colour, "colour_name": part_info.colour_name(colour), "description": part_info.describe(part),
                "needed": need, "owned": have, "reserved": held, "available": available, "missing": missing,
                "substitutes": [{"colour": c, "colour_name": part_info.colour_name(c), "available": q} for c, q in others[:6]],
                "maybe": maybe.get(key, 0)}
        if mode == "substitute":
            line["missing_with_substitutes"] = max(0, missing - sum(q for _, q in others))
        lines.append(line)
    lines.sort(key=lambda l: (-l["missing"], l["part"], l["colour"]))
    missing_total = sum(l["missing"] for l in lines)
    result = {"mode": mode, "needed": needed_total, "covered": covered, "missing": missing_total, "lines": lines}
    if mode == "substitute":
        result["missing_with_substitutes"] = sum(l["missing_with_substitutes"] for l in lines)
    return result


def export_shortage(report: dict, fmt: str) -> tuple[str, str, str]:
    """(body, media type, file extension) of a shortage list as plain text or CSV."""
    if fmt == "text":
        body = "".join(f"{l['missing']} x {l['part']} {l['description'] or ''} ({l['colour_name']})".replace("  ", " ") + "\n"
                       for l in report["lines"])
        return body or "Nothing is missing.\n", "text/plain; charset=utf-8", "txt"
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["part", "description", "colour", "colour_name", "needed", "owned", "reserved", "missing"])
        for l in report["lines"]:
            writer.writerow([_cell(l["part"]), _cell(l["description"] or ""), l["colour"], l["colour_name"], l["needed"], l["owned"],
                             l["reserved"], l["missing"]])
        return out.getvalue(), "text/csv; charset=utf-8", "csv"
    raise InventoryError(400, "format must be csv or text")


def _cell(text: str) -> str:
    """Spreadsheets run a cell that starts with = + - @ as a formula; part descriptions are catalogue text, not trusted."""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text
