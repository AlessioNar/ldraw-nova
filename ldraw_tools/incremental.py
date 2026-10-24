"""Validating an edited plan by what changed, not by rereading the whole model.

Every editor operation rebuilds the preview MPD, and `validate_text` over a 1,000-part model is most of what
that costs. After an edit nearly every line of the text is one a previous, error-free validation already
passed. `verdict` proves "no validation errors" for the edited text from three things:

* the placement lines that changed (plus one unchanged one per section) go through the very same
  `validate_text`, in a stripped copy of the text that keeps every FILE block, header and STEP line, so the
  per-line checks, header checks and parser diagnostics are the real ones;
* the checks that look across lines (two parts at the same spot, a part that still has the "current" colour 16
  at the top, the instance budget) are done on the plan itself, deliberately coarser than the original so they
  can only say "maybe" where the original would say "yes";
* everything else in the plan must be unchanged from a validated plan this process has seen (same sections,
  same headers, only plain placements).

Whenever any condition does not hold, `verdict` answers None and the caller validates the whole text as before,
so errors and their messages are exactly the full validation's. Only a "no errors" answer is ever shortcut.
"""
from __future__ import annotations

import collections
import json

import numpy as np

from .builder import rotation
from .common import normalized
from .validation import validate_text

PLAIN = frozenset({"id", "ref", "colour", "at", "purpose", "role", "yaw", "matrix"})
MAX_LEAVES = 50000        # beyond this the plan-level checks are not worth it; the text is validated whole
NEAR = 0.005              # LDU; the original compares at 1e-6, so anything within this is "maybe the same spot"


class Fingerprint:
    """A validated plan's shape: per-section headers and the JSON of every placement."""

    __slots__ = ("heads", "keys", "sets", "text", "parts")

    def __init__(self, plan):
        top = {k: v for k, v in plan.items() if k != "sections"}
        self.heads = [json.dumps([top, {k: v for k, v in s.items() if k != "steps"}], sort_keys=True)
                      for s in plan["sections"]]
        self.keys = [[json.dumps(entry, sort_keys=True) for step in s["steps"] for entry in step]
                     for s in plan["sections"]]
        self.sets = None
        self.text = None
        self.parts = None

    def members(self):
        if self.sets is None:
            self.sets = [set(keys) for keys in self.keys]
        return self.sets


_BASES: collections.deque[Fingerprint] = collections.deque(maxlen=8)


def remember(fingerprint, text, parts):
    """Record a plan whose built text has passed validation without errors against this parts library."""
    fingerprint.text, fingerprint.parts = text, parts
    _BASES.appendleft(fingerprint)


def forget():
    _BASES.clear()


def known_text(fingerprint, parts):
    """The text of an already validated plan with exactly this content, else None."""
    for base in _BASES:
        if base.parts is parts and base.heads == fingerprint.heads and base.keys == fingerprint.keys:
            return base.text
    return None


def fingerprint(plan):
    return Fingerprint(plan)


def _plain(plan):
    return all(PLAIN.issuperset(entry) for section in plan["sections"] for step in section["steps"] for entry in step)


def _stripped(text, masks):
    """`text` with the placement lines of unchanged entries removed (see `_mask`), CRLF kept; None if the text
    does not have the shape the plan says it has."""
    out, section, entry, lines = [], -1, -1, text.split("\r\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("0 FILE"):
            section, entry = section + 1, -1
        elif line.startswith("1 "):
            entry += 1
            if not masks[section][entry]:
                i += 1
                continue
        elif line.startswith("0 // ") and i + 1 < len(lines) and lines[i + 1].startswith("1 "):
            if not masks[section][entry + 1]:
                i += 2
                entry += 1
                continue
        out.append(line)
        i += 1
    if section != len(masks) - 1:
        return None
    return "\r\n".join(out)


def _mask(base_sets, fingerprint):
    """Per section, which placements to keep in the stripped text: the changed ones and the first unchanged."""
    masks = []
    for keys, known in zip(fingerprint.keys, base_sets):
        mask = [key not in known for key in keys]
        if keys and not any(mask):
            mask[0] = True
        masks.append(mask)
    return masks


def _structure(plan):
    """Per section: names of the child sections, leaf positions/rotations, and the colour-16 inheritance facts."""
    table = {normalized(s["name"]): s for s in plan["sections"]}
    arrays = {}
    for key, section in table.items():
        entries = [entry for step in section["steps"] for entry in step]
        position = np.array([entry["at"] for entry in entries], dtype=float).reshape(-1, 3)
        matrix = np.array([entry["matrix"] if "matrix" in entry else rotation("y", entry.get("yaw", 0)).rows
                           for entry in entries], dtype=float).reshape(-1, 3, 3)
        arrays[key] = (entries, position, matrix, [normalized(e["ref"]) if normalized(e["ref"]) in table else None
                                                    for e in entries])
    return table, arrays


def _leaf_count(key, arrays, memo):
    if key not in memo:
        entries, _, _, children = arrays[key]
        memo[key] = sum(1 if child is None else _leaf_count(child, arrays, memo) for child in children)
    return memo[key]


def _unresolved_colour(key, inherits_16, arrays, memo):
    """True when some leaf under `key` would still be colour 16 after inheritance (the original's error)."""
    if (key, inherits_16) not in memo:
        entries, _, _, children = arrays[key]
        found = False
        for entry, child in zip(entries, children):
            colour16 = entry["colour"] == 16 and inherits_16      # still "the current colour" after inheriting
            found = found or (colour16 if child is None else _unresolved_colour(child, colour16, arrays, memo))
        memo[(key, inherits_16)] = found
    return memo[(key, inherits_16)]


def _might_collide(root, arrays):
    """True when two placements of the same part may sit at the same world transform.

    The original reports a part placed twice at one transform (rounded to six decimals). Here two placements
    "may" coincide when they are the same part within NEAR of each other in all twelve numbers of position and
    rotation: a superset of what the original calls the same, found by sorting the positions into grids first.
    """
    refs, points, matrices = [], [], []

    def walk(key, rotation_matrix, offset):
        entries, position, matrix, children = arrays[key]
        world = position @ rotation_matrix.T + offset
        for i, child in enumerate(children):
            if child is None:
                refs.append(normalized(entries[i]["ref"]))
                points.append(world[i])
                matrices.append((rotation_matrix @ matrix[i]).ravel())
            else:
                walk(child, rotation_matrix @ matrix[i], world[i])

    walk(root, np.eye(3), np.zeros(3))
    if len(refs) < 2:
        return False
    ids = np.unique(np.array(refs), return_inverse=True)[1].reshape(-1, 1)
    points, matrices = np.array(points), np.array(matrices)
    for shift in range(8):      # two grids per axis, half a cell apart: a pair closer than NEAR shares a cell in one
        offset = np.array([(shift >> axis & 1) * NEAR for axis in range(3)])
        cells = np.floor((points + offset) / (2 * NEAR)).astype(np.int64)
        rows = np.hstack([ids, cells])
        _, inverse, counts = np.unique(rows, axis=0, return_inverse=True, return_counts=True)
        for group in np.nonzero(counts > 1)[0]:
            members = np.nonzero(inverse.ravel() == group)[0]
            for a, first in enumerate(members):
                close = np.all(np.abs(points[members[a + 1:]] - points[first]) < NEAR, axis=1) & np.all(
                    np.abs(matrices[members[a + 1:]] - matrices[first]) < NEAR, axis=1)
                if close.any():
                    return True
    return False


def verdict(plan, resolved, text, parts, fingerprint, instance_limit=100000):
    """True when `text` (built from `plan`) provably has no validation errors; None when it must be validated whole.

    `resolved` is the plan with symbols resolved. `fingerprint` is `Fingerprint(plan)`.
    """
    if not _plain(plan) or plan.get("assets") or plan.get("includes"):
        return None
    base = next((b for b in _BASES if b.parts is parts and b.heads == fingerprint.heads), None)
    if base is None:
        return None
    if base.text == text:
        return True
    masks = _mask(base.members(), fingerprint)
    try:
        stripped = _stripped(text, masks)
    except IndexError:
        return None
    if stripped is None:
        return None
    _, problems = validate_text(stripped, parts, name=resolved["sections"][0]["name"], instance_limit=instance_limit)
    if any(p["severity"] == "error" and p["code"] != "assembly.empty" for p in problems):
        return None
    table, arrays = _structure(resolved)
    root = normalized(resolved["sections"][0]["name"])
    leaves = _leaf_count(root, arrays, {})
    if not 0 < leaves <= min(MAX_LEAVES, instance_limit):
        return None
    if _unresolved_colour(root, True, arrays, {}) or _might_collide(root, arrays):
        return None
    return True
