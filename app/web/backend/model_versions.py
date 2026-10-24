"""What changed between two revisions of a chat's model, computed from their two BOMs (no model involved)."""
from __future__ import annotations

import csv
from pathlib import Path

from leocad_render import bom_path_for

MAX_CLAUSES = 3                       # added / removed / recoloured parts named in the one-line summary


def bom_rows(model: Path) -> dict[str, dict]:
    """part id -> {"name", "colours": {colour name: quantity}} from the model's BOM, or {} when there is none."""
    try:
        with bom_path_for(model).open(newline="", encoding="utf-8", errors="replace") as fh:
            rows: dict[str, dict] = {}
            for row in csv.DictReader(fh):
                part = (row.get("Part ID") or "").strip().lower().removesuffix(".dat")
                if not part:
                    continue
                entry = rows.setdefault(part, {"name": " ".join((row.get("Part Name") or part).split()), "colours": {}})
                colour = (row.get("Color") or row.get("Color Code") or "?").strip()
                entry["colours"][colour] = entry["colours"].get(colour, 0) + int(row.get("Quantity") or 0)
            return rows
    except (OSError, ValueError, KeyError):
        return {}


def _count(rows: dict[str, dict]) -> int:
    return sum(q for r in rows.values() for q in r["colours"].values())


def diff(previous: Path, current: Path) -> dict | None:
    """{delta, total, added, removed, recoloured, summary} between two models, None when a BOM is missing.

    Lines are (part, colour) pairs: a part that lost n in one colour and gained n in another is recoloured;
    what is left over is added or removed.
    """
    before, after = bom_rows(previous), bom_rows(current)
    if not before or not after:
        return None
    added, removed, recoloured = [], [], []
    for part in sorted(set(before) | set(after)):
        name = (after.get(part) or before[part])["name"]
        old, new = (before.get(part) or {"colours": {}})["colours"], (after.get(part) or {"colours": {}})["colours"]
        gains = {c: new.get(c, 0) - old.get(c, 0) for c in set(old) | set(new)}
        lost = {c: -d for c, d in sorted(gains.items()) if d < 0}
        gained = {c: d for c, d in sorted(gains.items()) if d > 0}
        for src in lost:
            for dst in gained:
                n = min(lost[src], gained[dst])
                if n > 0:
                    recoloured.append({"part": part, "name": name, "from": src, "to": dst, "qty": n})
                    lost[src] -= n
                    gained[dst] -= n
        added += [{"part": part, "name": name, "colour": c, "qty": n} for c, n in gained.items() if n > 0]
        removed += [{"part": part, "name": name, "colour": c, "qty": n} for c, n in lost.items() if n > 0]
    delta, total = _count(after) - _count(before), _count(after)
    return {"delta": delta, "total": total, "added": added, "removed": removed, "recoloured": recoloured,
            "summary": summary(delta, total, added, removed, recoloured)}


def summary(delta: int, total: int, added: list, removed: list, recoloured: list) -> str:
    if not (added or removed or recoloured):
        return "No part changes"
    head = f"{delta:+d} part{'s' if abs(delta) != 1 else ''} ({total} total)" if delta else f"{total} parts, same count"

    def clause(verb: str, items: list, text) -> str:
        shown = ", ".join(text(i) for i in items[:MAX_CLAUSES])
        more = f" and {len(items) - MAX_CLAUSES} more" if len(items) > MAX_CLAUSES else ""
        return f"{verb} {shown}{more}"

    parts = [head]
    if added:
        parts.append(clause("added", added, lambda i: f"{i['qty']} x {i['name']} ({i['colour']})"))
    if removed:
        parts.append(clause("removed", removed, lambda i: f"{i['qty']} x {i['name']} ({i['colour']})"))
    if recoloured:
        parts.append(clause("recoloured", recoloured, lambda i: f"{i['qty']} x {i['name']} {i['from']} to {i['to']}"))
    return "; ".join(parts)
