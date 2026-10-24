"""Manual plan editing: operations, inverse history, rebuild and snapping.

The plan stays the source of truth: every operation is a plain dict checked
against plan.schema.json, `apply` never mutates its input, and `rebuild` goes
through the same check_schema + build_plan path the agent uses.
"""
from __future__ import annotations

import copy
import math
import re

import numpy as np
from ldraw import Piece
from ldraw.lines import Comment

from .builder import build_plan, check_schema, repeated, rotation
from .common import jsonable, normalized

PUBLIC_OPS = ("add", "move", "rotate", "recolour", "delete", "snap")
HISTORY_OPS = ("set_entry", "insert")
OPS = (*PUBLIC_OPS, *HISTORY_OPS)
POSITIONAL_KEYS = ("at", "on", "offset_studs", "attach", "snap")


def _fail(message):
    raise ValueError(message)


def _check_plan(plan):
    try:
        check_schema(plan)
    except Exception as exc:
        _fail(f"Not a valid plan: {getattr(exc, 'message', exc)}")


def _vector(value, what):
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        _fail(f"{what} must be three finite numbers")
    if any(abs(float(v)) > 1000000 for v in value):
        _fail(f"{what} is outside the plan's coordinate range")
    return [float(v) for v in value]


def _colour(value, parts):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        _fail(f"colour {value!r} must be an LDraw colour code or a symbolic colour")
    if isinstance(value, str) and not re.fullmatch(r"@colours\.[A-Za-z_][A-Za-z0-9_]*", value):
        _fail(f"colour {value!r} is not a valid symbolic colour")
    if parts is not None and isinstance(value, int) and (value not in parts.colours_by_code or value == 24):
        _fail(f"Unknown or unsuitable colour {value}; pick a code from the LDraw colour table")
    return value


def _part(plan, value, parts):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*\.(dat|ldr)", value):
        _fail(f"part {value!r} is not a valid part or submodel reference")
    if parts is not None:
        sections = {normalized(s["name"]) for s in plan["sections"]}
        code = value.casefold().removesuffix(".dat")
        if normalized(value) not in sections and parts.find_part(code=code) is None:
            _fail(f"Unknown part {value}; search the library before adding it")
    return value


def _degrees(value, what="degrees"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(f"{what} must be a finite number")
    return float(value)


def _axis(value):
    if value not in {"x", "y", "z"}:
        _fail("axis must be x, y or z")
    return value


def _clean(value):
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, float):
        result = round(value + 0.0, 9) + 0.0
        return int(result) if result == int(result) else result
    return value


def _wrap(degrees):
    result = degrees % 360
    return result if result <= 180 else result - 360


def _section(plan, name):
    if not isinstance(name, str):
        _fail("section must be a section name")
    for index, section in enumerate(plan["sections"]):
        if normalized(section["name"]) == normalized(name):
            return index
    _fail(f"Unknown section {name!r}")


def _locate(plan, edit):
    entry_id = edit.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        _fail("an entry id is required")
    wanted = edit.get("section")
    matches = []
    for si, section in enumerate(plan["sections"]):
        if wanted is not None and normalized(section["name"]) != normalized(wanted):
            continue
        for ti, step in enumerate(section["steps"]):
            for ei, entry in enumerate(step):
                if entry["id"] == entry_id:
                    matches.append((si, ti, ei))
    if len(matches) > 1:
        names = sorted({plan["sections"][m[0]]["name"] for m in matches})
        _fail(f"Entry id {entry_id} appears in more than one section ({', '.join(names)}); pass a section")
    if not matches:
        _fail(f"Unknown entry id {entry_id}")
    return matches[0]


def _next_id(plan):
    used = {entry["id"] for section in plan["sections"] for step in section["steps"] for entry in step}
    count = 1
    while f"e{count}" in used:
        count += 1
    return f"e{count}"


def _validate(plan, edit, parts):
    if not isinstance(edit, dict):
        _fail("each edit must be a dict with an op")
    op = edit.get("op")
    if op not in OPS:
        _fail(f"Unknown operation {op!r}; choose one of {', '.join(OPS)}")
    if op == "add":
        _section(plan, edit.get("section"))
        _part(plan, edit.get("part"), parts)
        _colour(edit.get("colour"), parts)
        _vector(edit.get("position"), "position")
        requested = edit.get("rotation")
        if requested is not None:
            if isinstance(requested, dict):
                _axis(requested.get("axis"))
                _degrees(requested.get("degrees"))
            else:
                _degrees(requested, "rotation")
        if edit.get("purpose") is not None and (not isinstance(edit["purpose"], str) or len(edit["purpose"]) > 500):
            _fail("purpose must be a short string")
    elif op in ("move", "rotate", "recolour", "delete", "snap", "set_entry"):
        _locate(plan, edit)
        if op == "move":
            _vector(edit.get("position"), "position")
        elif op == "rotate":
            _axis(edit.get("axis"))
            _degrees(edit.get("degrees"))
        elif op == "recolour":
            _colour(edit.get("colour"), parts)
        elif op == "snap" and edit.get("to") is not None and not isinstance(edit["to"], str):
            _fail("snap target must be an entry id")
    elif op == "insert":
        _section(plan, edit.get("section"))
        entry = edit.get("entry")
        if not isinstance(entry, dict) or not entry.get("id"):
            _fail("insert needs the full entry to restore")


def _rotation_fields(axis, degrees):
    if abs(degrees) < 1e-9:
        return {}
    if axis == "y":
        return {"yaw": _clean(_wrap(degrees))}
    return {"matrix": _clean(np.array(rotation(axis, degrees).rows, dtype=float).tolist())}


def _added_entry(plan, edit, parts):
    entry = {"id": _next_id(plan), "ref": _part(plan, edit["part"], parts), "colour": _colour(edit["colour"], parts)}
    entry["at"] = _vector(edit["position"], "position")
    requested = edit.get("rotation")
    if requested is not None:
        if isinstance(requested, dict):
            axis, degrees = _axis(requested.get("axis")), _degrees(requested.get("degrees"))
        else:
            axis, degrees = "y", _degrees(requested, "rotation")
        entry.update(_rotation_fields(axis, degrees))
    if edit.get("purpose") is not None:
        entry["purpose"] = edit["purpose"]
    return entry


def _moved_entry(entry, position):
    result = {"id": entry["id"], "ref": entry["ref"], "colour": entry["colour"], "at": position}
    for key in ("purpose", "role", "yaw", "matrix", "repeat"):
        if key in entry:
            result[key] = copy.deepcopy(entry[key])
    return result


def _rotated_entry(entry, axis, degrees):
    if "attach" in entry:
        _fail(f"entry {entry['id']} is attach-placed; its orientation comes from the module anchor")
    if "repeat" in entry:
        _fail(f"entry {entry['id']} is a repeated placement; rotate its template in the plan source")
    result = copy.deepcopy(entry)
    world = np.array(rotation(axis, degrees).rows, dtype=float)
    if "matrix" in result:
        combined = (world @ np.array(result["matrix"], dtype=float)).tolist()
        del result["matrix"]
        if np.allclose(combined, np.eye(3), atol=1e-9):
            return result
        result["matrix"] = _clean(combined)
    elif "yaw" in result:
        if axis == "y":
            yaw = _wrap(result["yaw"] + degrees)
            del result["yaw"]
            if yaw:
                result["yaw"] = _clean(yaw)
        else:
            result["matrix"] = _clean((world @ np.array(rotation("y", result["yaw"]).rows, dtype=float)).tolist())
            del result["yaw"]
    else:
        result.update(_rotation_fields(axis, degrees))
    return result


def _apply_op(plan, edit, parts):
    op = edit["op"]
    if op == "add":
        section = plan["sections"][_section(plan, edit["section"])]
        section["steps"][-1].append(_added_entry(plan, edit, parts))
    elif op == "move":
        si, ti, ei = _locate(plan, edit)
        step = plan["sections"][si]["steps"][ti]
        step[ei] = _moved_entry(step[ei], _vector(edit["position"], "position"))
    elif op == "rotate":
        si, ti, ei = _locate(plan, edit)
        step = plan["sections"][si]["steps"][ti]
        step[ei] = _rotated_entry(step[ei], _axis(edit["axis"]), _degrees(edit["degrees"]))
    elif op == "recolour":
        si, ti, ei = _locate(plan, edit)
        plan["sections"][si]["steps"][ti][ei]["colour"] = _colour(edit["colour"], parts)
    elif op == "delete":
        si, ti, ei = _locate(plan, edit)
        section = plan["sections"][si]
        del section["steps"][ti][ei]
        if not section["steps"][ti]:
            if len(section["steps"]) == 1:
                _fail(f"Cannot delete {edit['id']}: section {section['name']} would have no steps left")
            del section["steps"][ti]
    elif op == "set_entry":
        si, ti, ei = _locate(plan, edit)
        plan["sections"][si]["steps"][ti][ei] = copy.deepcopy(edit["entry"])
    elif op == "insert":
        steps = plan["sections"][_section(plan, edit["section"])]["steps"]
        entry = copy.deepcopy(edit["entry"])
        if edit.get("recreate_step"):
            steps.insert(min(edit["step"], len(steps)), [entry])
        else:
            if not 0 <= edit["step"] < len(steps):
                _fail(f"insert step {edit['step']} is out of range")
            steps[edit["step"]].insert(min(edit["index"], len(steps[edit["step"]])), entry)
    elif op == "snap":
        _apply_snap(plan, edit, parts)
    else:
        _fail(f"Unknown operation {op!r}")


def _earlier_entries(section, step_index, entry_index):
    result = []
    for ti, step in enumerate(section["steps"]):
        for ei, entry in enumerate(step):
            if (ti, ei) < (step_index, entry_index):
                result.append(entry)
    return result


def _model_placements(model):
    result, current = [], None
    for obj in model.objects:
        text = getattr(obj, "text", None)
        if isinstance(obj, Comment) and text.startswith("// "):
            current = text[3:].split(":", 1)[0] or None
        elif isinstance(obj, Piece) and current:
            position = jsonable(obj.position)
            result.append((current, [_clean(v) for v in position], _clean(obj.matrix.rows)))
            current = None
    return result


def placements(plan, parts):
    """Resolved section-local placement of every expanded entry, keyed (section, id)."""
    from .document import section_table
    _, parsed, _ = build_plan(plan, parts)
    table = section_table(parsed)
    result = {}
    for section in plan["sections"]:
        model = table.get(normalized(section["name"]))
        for entry_id, at, matrix in _model_placements(model):
            result[(section["name"], entry_id)] = {"at": at, "matrix": matrix}
    return result


def _snap_target(plan, section, si, ti, ei, edit, parts):
    candidates = _earlier_entries(section, ti, ei)
    repeated = [e for e in candidates if "repeat" in e]
    wanted = edit.get("to")
    if wanted is not None:
        match = next((e for e in candidates if e["id"] == wanted), None)
        if match is None:
            _fail(f"snap target {wanted} must be an entry placed before it in {section['name']}")
        if match in repeated:
            _fail(f"snap target {wanted} is a repeated placement")
        return match
    resolved = placements(plan, parts)
    origin = _vector(section["steps"][ti][ei]["at"], "position")
    def distance(entry):
        at = resolved.get((section["name"], entry["id"]), {}).get("at", entry.get("at"))
        if at is None:
            return float("inf")
        return sum((a - b) ** 2 for a, b in zip(at, origin))
    singles = [e for e in candidates if "repeat" not in e]
    if not singles:
        _fail(f"entry {section['steps'][ti][ei]['id']} has nothing to snap to in {section['name']}")
    return min(singles, key=distance)


def _apply_snap(plan, edit, parts):
    si, ti, ei = _locate(plan, edit)
    section = plan["sections"][si]
    entry = section["steps"][ti][ei]
    if "repeat" in entry:
        _fail(f"entry {entry['id']} is a repeated placement; snap its template in the plan source")
    if "at" not in entry:
        _fail(f"entry {entry['id']} has no explicit position; only dropped parts can snap")
    target = _snap_target(plan, section, si, ti, ei, edit, parts)
    # `nearest`: of the ways to connect the two parts, take the one closest to where the part was dropped.
    snapped = {"id": entry["id"], "ref": entry["ref"], "colour": entry["colour"],
               "snap": {"to": target["id"], "near": copy.deepcopy(entry["at"]), "nearest": True}}
    for key in ("purpose", "role", "yaw", "matrix"):
        if key in entry:
            snapped[key] = copy.deepcopy(entry[key])
    probe = copy.deepcopy(plan)
    probe["sections"][si]["steps"][ti][ei] = snapped
    try:
        _, parsed, _ = build_plan(probe, parts)
    except ValueError as exc:
        _fail(f"snap {entry['id']}: {exc}")
    from .document import section_table
    table = section_table(parsed)
    resolved = {entry_id: (at, matrix) for entry_id, at, matrix in _model_placements(table.get(normalized(section["name"])))}
    if entry["id"] not in resolved:
        _fail(f"snap {entry['id']}: the build did not place it")
    at, matrix = resolved[entry["id"]]
    baked = {"id": entry["id"], "ref": entry["ref"], "colour": entry["colour"], "at": at}
    if "purpose" in entry:
        baked["purpose"] = entry["purpose"]
    if "role" in entry:
        baked["role"] = entry["role"]
    if not np.allclose(matrix, np.eye(3), atol=1e-9):
        baked["matrix"] = matrix
    section["steps"][ti][ei] = baked


def apply(plan, edits, parts=None):
    """Apply a list of edit operations; return a new plan, never mutating the input."""
    if not isinstance(edits, (list, tuple)):
        _fail("edits must be a list of operations")
    _check_plan(plan)
    result = copy.deepcopy(plan)
    for edit in edits:
        # Each edit is checked against the plan the earlier edits produced, so one batch can add a part and
        # snap it (or move something it just added); any invalid edit still rejects the whole batch.
        _validate(result, edit, parts)
        _apply_op(result, edit, parts)
    try:
        check_schema(result)
    except Exception as exc:
        _fail(f"the edits produced an invalid plan: {getattr(exc, 'message', exc)}")
    return result


def inverse(plan, edits, parts=None):
    """Inverse operations that restore `plan` after `apply(plan, edits)`.

    Undo them in reverse order. Inverses are operations, not plan copies.
    """
    if not isinstance(edits, (list, tuple)):
        _fail("edits must be a list of operations")
    _check_plan(plan)
    working, result = copy.deepcopy(plan), []
    for edit in edits:
        _validate(working, edit, parts)
        result.append(_inverse_op(working, edit, parts))
        _apply_op(working, edit, parts)
    return result


def _inverse_op(plan, edit, parts):
    op = edit["op"]
    if op == "add":
        section = plan["sections"][_section(plan, edit["section"])]
        return {"op": "delete", "id": _added_entry(plan, edit, parts)["id"], "section": section["name"]}
    if op == "insert":
        return {"op": "delete", "id": edit["entry"]["id"], "section": edit["section"]}
    si, ti, ei = _locate(plan, edit)
    section = plan["sections"][si]
    current = copy.deepcopy(section["steps"][ti][ei])
    if op == "delete":
        return {"op": "insert", "section": section["name"], "step": ti, "index": ei,
                "entry": current, "recreate_step": len(section["steps"][ti]) == 1}
    return {"op": "set_entry", "id": current["id"], "section": section["name"], "entry": current}


def _attribute(plan, message):
    for pattern, key in ((r"Unknown reference ([^;]+);", "ref"), (r"colour (\d+)", "colour")):
        match = re.search(pattern, message)
        if not match:
            continue
        for section in plan["sections"]:
            for step in section["steps"]:
                for entry in step:
                    if str(entry[key]) == match.group(1):
                        return f"entry {entry['id']} in {section['name']}: {message}"
    return message


def rebuild(plan, parts):
    """Schema-check the plan, build it, and return its MPD text."""
    check_schema(plan)
    try:
        text, _, diagnostics = build_plan(plan, parts)
    except ValueError as exc:
        raise ValueError(_attribute(plan, str(exc))) from None
    errors = [d for d in diagnostics if d["severity"] == "error"]
    if errors:
        raise ValueError("Rebuild failed validation: " + "; ".join(d["message"] for d in errors[:3]))
    return text


def snap(plan, entry_id, parts, to=None):
    """Lock an explicitly placed entry onto the nearest earlier connection."""
    return apply(plan, [{"op": "snap", "id": entry_id, "to": to}], parts)


def usage(plan, parts=None):
    """Leaf pieces per (part, colour), expanded the way the build counts them.

    Repeats count per copy, submodel references recurse, and colour 16 inside a
    referenced section inherits the referencing placement's colour. Only the
    root section is walked: parts in unreferenced sections are not in the model.
    Keys match `inventory.model_bom`: the part number without .dat, casefolded.
    """
    if parts is not None:
        from .catalog import resolve_plan
        plan = resolve_plan(plan, parts)
    sections = {normalized(s["name"]): s for s in plan["sections"]}
    result: dict[tuple[str, int], int] = {}

    def walk(section, inherit):
        for step in section["steps"]:
            for entry in repeated(step):
                colour = entry["colour"]
                ref = normalized(entry["ref"])
                if ref in sections:
                    walk(sections[ref], colour if colour != 16 else inherit)
                    continue
                key = (ref.removesuffix(".dat"), colour if colour != 16 else inherit)
                result[key] = result.get(key, 0) + 1

    walk(plan["sections"][0], 16)
    return result


# ------------------------------------------------------------------ MPD import

_ID = re.compile(r"[A-Za-z0-9_-]+")
_SECTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]*\.ldr")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]*\.(dat|ldr)")


def _section_name(raw, taken):
    """A schema-valid, unique section name for an MPD file name (the references are remapped by the caller)."""
    name = raw.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not _SECTION.fullmatch(name):
        stem = re.sub(r"[^A-Za-z0-9 _-]", "_", name.rsplit(".", 1)[0]).strip(" _-") or "model"
        name = f"{stem}.ldr"[:200]
    base, count = name, 2
    while normalized(name) in taken:
        name, count = f"{base[:-4]}-{count}.ldr", count + 1
    return name


def import_mpd(text, parts=None, name="model.ldr"):
    """Turn MPD/LDR text into an editable plan: `(plan, warnings)`.

    The model's structure is kept, not flattened: each `0 FILE` becomes a section, each `0 STEP` a step,
    and each type-1 line a placement with its exact position and rotation matrix (`// id: purpose` comments
    from the builder restore the entry ids). Anything that cannot be edited is skipped with a warning:
    lines of other types (primitives, 2-5), direct RGB colours, references the library does not know,
    and empty submodels. The first FILE is the root model.
    """
    files, current, lines = [], None, text.splitlines()
    for line in lines:
        words = line.split()
        if len(words) >= 3 and words[0] == "0" and words[1] == "FILE":
            current = {"raw": line.split(None, 2)[2].strip(), "lines": []}
            files.append(current)
        elif words[:2] == ["0", "NOFILE"]:
            current = None
        elif current is None and files == [] and words:
            current = {"raw": name, "lines": []}
            files.append(current)
            current["lines"].append(line)
        elif current is not None:
            current["lines"].append(line)
    if not files:
        raise ValueError("The file has no model in it")
    taken, names = set(), {}
    for item in files:
        item["name"] = _section_name(item["raw"], taken)
        taken.add(normalized(item["name"]))
        names.setdefault(normalized(item["raw"].replace("\\", "/").rsplit("/", 1)[-1]), item["name"])
    warnings, author, sections = [], None, []
    for item in files:
        steps, step, description, pending, ids = [], [], None, None, set()
        for line in item["lines"]:
            words = line.split()
            if not words:
                continue
            kind = words[0]
            if kind == "0":
                body = line.split(None, 1)[1].strip() if len(words) > 1 else ""
                if body == "STEP" or body.startswith("STEP "):
                    if step:
                        steps.append(step)
                    step = []
                elif body.startswith("// "):
                    pending = body[3:]
                elif body.startswith("Author:"):
                    author = author or body[7:].strip()
                elif description is None and body and not step and not steps and not body.startswith(("Name:", "!", "//")):
                    description = body
                continue
            where = f"{item['name']}: "
            if kind != "1":
                warnings.append(f"{where}skipped a type {kind} line ({line.strip()[:60]})")
                pending = None
                continue
            try:
                if len(words) < 15:
                    raise ValueError("too few fields")
                colour = int(words[1])
                numbers = [float(v) for v in words[2:14]]
                ref = " ".join(words[14:]).replace("\\", "/").lower()
            except ValueError:
                warnings.append(f"{where}skipped an unreadable line ({line.strip()[:60]})")
                pending = None
                continue
            target = names.get(normalized(ref))
            ref = target or ref
            if not target and (not _REF.fullmatch(ref) or (
                    parts is not None and parts.find_part(code=ref.removesuffix(".dat")) is None)):
                warnings.append(f"{where}skipped {ref}: not a part in the library")
                pending = None
                continue
            if colour < 0 or colour == 24 or (parts is not None and colour not in parts.colours_by_code):
                warnings.append(f"{where}skipped {ref}: colour {colour} is not an editable library colour")
                pending = None
                continue
            entry_id, _, purpose = (pending or "").partition(": ")
            if not _ID.fullmatch(entry_id) or entry_id in ids:
                entry_id, purpose = f"p{len(ids) + 1}", ""
                while entry_id in ids:
                    entry_id = f"p{int(entry_id[1:]) + 1}"
            ids.add(entry_id)
            entry = {"id": entry_id, "ref": ref, "colour": colour, "at": [_clean(v) for v in numbers[:3]]}
            if purpose and purpose != ref and len(purpose) <= 500:
                entry["purpose"] = purpose
            matrix = [numbers[3:6], numbers[6:9], numbers[9:12]]
            if not np.allclose(matrix, np.eye(3), atol=1e-9):
                entry["matrix"] = _clean(matrix)
            step.append(entry)
            pending = None
        if step:
            steps.append(step)
        sections.append({"name": item["name"], "description": (description or item["name"])[:500],
                         "steps": steps})
    while True:   # an empty submodel cannot be a section; drop it and the placements that used it
        empty = {normalized(s["name"]) for s in sections if not s["steps"]}
        if not empty or sections[0]["name"] and normalized(sections[0]["name"]) in empty:
            break
        for section in sections:
            kept = []
            for step in section["steps"]:
                step[:] = [e for e in step if normalized(e["ref"]) not in empty]
                if step:
                    kept.append(step)
            section["steps"] = kept
        for gone in [s["name"] for s in sections if not s["steps"]]:
            warnings.append(f"dropped {gone}: no placements left")
        sections = [s for s in sections if s["steps"]]
    if not sections or not sections[0]["steps"]:
        raise ValueError("The model has no editable parts in it")
    plan = {"version": 1, "author": (author or "imported")[:500], "sections": sections}
    check_schema(plan)
    return plan, warnings
