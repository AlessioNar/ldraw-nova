"""Palette routes for the manual editor: the colour table, a part search and the inventory gate.

Searches reuse the toolkit CLI the agent uses (`ldraw-agent search parts`, `ldraw-agent inventory
search`). Owned quantities come from the chat's inventories, used quantities from the editor plan's
used rows (state.json), so "available" is owned minus used and is never stored.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import parts_steering
import sandbox
import settings
import toolkit
from store import get_store
from .editor import MAX_ROWS, _chat, _key, _session

router = APIRouter()

TIMEOUT = 120
MAX_RESULTS = 50
EXCLUDED_COLOURS = {16, 24}          # "main colour" and "edge colour" are not colours a person picks
COLOUR_LINE = re.compile(r"0\s+!COLOUR\s+(\S+)\s+CODE\s+(\d+)\s+VALUE\s+(#[0-9A-Fa-f]{6})")
PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]*(\.dat)?")
REFUSAL = "The inventory cannot cover this edit"     # same wording as the editor CLI's refusal


@lru_cache(maxsize=2)
def _colour_table(library: str) -> tuple[tuple[int, str, str], ...]:
    rows: dict[int, tuple[int, str, str]] = {}
    try:
        for line in (Path(library) / "LDConfig.ldr").read_text(encoding="utf-8", errors="replace").splitlines():
            m = COLOUR_LINE.match(line.strip())
            if m and int(m.group(2)) not in EXCLUDED_COLOURS:
                code = int(m.group(2))
                rows.setdefault(code, (code, m.group(1).replace("_", " "), m.group(3).upper()))
    except OSError:
        pass
    return tuple(sorted(rows.values()))


def colours() -> list[dict]:
    """Every colour of the LDraw table the editor accepts: code, name, hex swatch."""
    return [{"code": c, "name": n, "hex": h} for c, n, h in _colour_table(str(settings.LDRAW_DIR))]


def part_key(part: str) -> str:
    return str(part).strip().casefold().removesuffix(".dat")


def owned_map(inventories) -> dict[tuple[str, int], int]:
    owned: dict[tuple[str, int], int] = {}
    for inventory in inventories:
        for part, colour, qty in inventory.rows:
            key = (part_key(part), colour)
            owned[key] = owned.get(key, 0) + qty
    return owned


def used_map(used_rows) -> dict[tuple[str, int], int]:
    used: dict[tuple[str, int], int] = {}
    for part, colour, qty in (used_rows or [])[:MAX_ROWS]:
        key = (part_key(part), colour)
        used[key] = used.get(key, 0) + qty
    return used


def check_add(mode: str, owned: dict, used: dict, part: str, colour: int, quantity: int = 1) -> dict:
    """The inventory gate for adding `quantity` of (part, colour): refused in "only", a warning in "prefer"."""
    key = (part_key(part), colour)
    have, in_use = owned.get(key, 0), used.get(key, 0)
    needed = in_use + quantity
    short = mode in ("prefer", "only") and needed > have
    result = {"allowed": not (short and mode == "only"), "warning": None, "message": None,
              "owned": have if mode != "off" else None, "used": in_use,
              "available": max(have - in_use, 0) if mode != "off" else None}
    if short:
        result["short"] = [[key[0], colour, needed, have]]
        if mode == "only":
            result["message"] = REFUSAL
        else:
            result["warning"] = (f"Not covered by your inventory ({have} owned, {needed} needed)"
                                 if have else "Not in your inventory")
    return result


def palette_items(parts: list[dict], owned: dict, used: dict, mode: str) -> list[dict]:
    """Group search hits (part, description) with per-colour owned, used and available."""
    items = []
    for hit in parts:
        key = part_key(hit["part"])
        rows = []
        if mode != "off":
            for (part, colour), qty in sorted(owned.items()):
                if part == key:
                    in_use = used.get((part, colour), 0)
                    rows.append({"colour": colour, "name": parts_steering.colour_name(colour), "owned": qty,
                                 "used": in_use, "available": max(qty - in_use, 0)})
        available = sum(r["available"] for r in rows)
        items.append({"part": key, "ref": f"{key}.dat", "description": hit["description"], "colours": rows,
                      "in_inventory": bool(rows),
                      "owned_total": sum(r["owned"] for r in rows) if mode != "off" else None,
                      "available_total": available if mode != "off" else None,
                      "used_up": mode != "off" and bool(rows) and available == 0})
    return items


async def _cli(store, chat_id: str, arguments: list[str]) -> dict:
    result = await sandbox.run(["./ldraw-agent", *arguments], cwd=toolkit.workspace(store, chat_id),
                               timeout=TIMEOUT, env_extra=toolkit.environment())
    if result.timed_out or result.exit_code is None:
        raise HTTPException(504, "The part search timed out")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(500, f"The part search did not report JSON (exit code {result.exit_code}): {(result.stderr or result.stdout or "")[-300:]}") from None


class PaletteCheck(BaseModel):
    part: str
    colour: int
    quantity: int = 1


def _known_colour(code: int) -> None:
    if code not in {c["code"] for c in colours()}:
        raise HTTPException(400, f"Colour {code} is not in the LDraw colour table")


@router.get("/api/editor/colours")
def editor_colours():
    return {"colours": colours()}


@router.get("/api/{space}/{chat_id}/editor/palette")
async def editor_palette(space: str, chat_id: str, query: str = "", colour: Optional[int] = None,
                         inventory_only: bool = False, limit: int = 20):
    """Search parts by number or name. `inventory_only` searches the chat's selected inventories only."""
    store = get_store()
    chat_id = _key(space, chat_id)
    chat = _chat(store, chat_id)
    limit = max(1, min(limit, MAX_RESULTS))
    query = query.strip()[:200]
    mode, inventories = parts_steering.selection(chat.get("options") or {})
    session = _session(store, chat_id)
    used = used_map(session[1].get("used") if session else None)
    owned = owned_map(inventories)
    if inventory_only and (mode == "off" or not inventories):
        raise HTTPException(400, "Choose parts inventories for this chat to search them")
    if colour is not None:
        _known_colour(colour)
    hits: list[dict] = []
    if inventory_only:
        report = await _cli(store, chat_id, ["inventory", "search", parts_steering.id_list(inventories), "--limit", "500",
                                             "--json", "--store", str(settings.INVENTORIES_DIR),
                                             *(["--colour", str(colour)] if colour is not None else []),
                                             *(["--", query] if query else [])])
        if report.get("error"):
            raise HTTPException(400, str(report["error"])[:300])
        seen: set[str] = set()
        for match in report.get("matches") or []:
            key = part_key(match["part"])
            if key not in seen:
                seen.add(key)
                hits.append({"part": key, "description": parts_steering.safe_name(match.get("description"), 80) if match.get("description") else ""})
    elif query:
        report = await _cli(store, chat_id, ["search", "parts", "--limit", str(limit + 1), "--", query])
        if report.get("error"):
            raise HTTPException(400, str(report["error"])[:300])
        hits = [{"part": part_key(r["code"]), "description": r["description"]} for r in report.get("results") or []]
    items = palette_items(hits[:limit], owned, used, mode)
    if colour is not None:
        for item in items:
            item["colours"] = [c for c in item["colours"] if c["colour"] == colour]
    return {"mode": mode, "items": items, "more": len(hits) > limit}


@router.post("/api/{space}/{chat_id}/editor/palette/check")
def editor_palette_check(space: str, chat_id: str, body: PaletteCheck):
    """Would adding this part in this colour be allowed, and is it covered? Same rules the edit gate applies."""
    store = get_store()
    chat_id = _key(space, chat_id)
    chat = _chat(store, chat_id)
    _known_colour(body.colour)
    if not PART_RE.fullmatch(body.part.strip()) or not 1 <= body.quantity <= 1000:
        raise HTTPException(400, "Not a valid part number or quantity")
    mode, inventories = parts_steering.selection(chat.get("options") or {})
    session = _session(store, chat_id)
    return check_add(mode, owned_map(inventories), used_map(session[1].get("used") if session else None),
                     body.part, body.colour, body.quantity)
