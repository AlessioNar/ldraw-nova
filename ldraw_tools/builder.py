"""Deterministic, schema-checked assembly plans using pyldraw3's writer."""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
from pathlib import Path

import jsonschema
import numpy as np
from ldraw import Model, Piece, Vector, Matrix
from ldraw.lines import Comment

from .common import DATA, normalized, jsonable
from .geometry import profiles
from .validation import validate_text


@functools.lru_cache(maxsize=1)
def _validators():
    """(whole-plan validator, plan validator with placements left open, placement validator, step length limit).

    The placement is the bulk of a big plan and is checked on its own: an edit changes a few entries, so
    entries that already passed (remembered by their JSON) are not validated again.
    """
    schema = json.loads((DATA / "plan.schema.json").read_text())
    defs = schema["$defs"]
    # `placement` stays addressable for the "#/$defs/placement/properties/matrix" ref: `True` cannot be descended into.
    open_placement = {"properties": {"matrix": defs["placement"]["properties"]["matrix"]}}
    shell = {**schema, "$defs": {**defs, "placement": open_placement}}
    entry = {"$schema": schema["$schema"], "$ref": "#/$defs/placement", "$defs": defs}
    make = jsonschema.Draft202012Validator
    return make(schema), make(shell), make(entry), defs["section"]["properties"]["steps"]["items"]["maxItems"]


_VALID_PLANS: set[bytes] = set()       # digests of whole plans that passed
_VALID_ENTRIES: set[str] = set()       # JSON of placements that passed
MAX_REMEMBERED = 100000


def _remember(known, items):
    if len(known) + len(items) > MAX_REMEMBERED:
        known.clear()
    known.update(items)


def check_schema(plan):
    """Raise jsonschema.ValidationError unless `plan` fits plan.schema.json (same verdict as validating it whole).

    Verdicts are remembered by content: a plan checked before costs one JSON encoding, and a plan that differs
    by a few placements validates only those (the rest of the document is checked with placements left open).
    """
    full, shell, entry, step_limit = _validators()
    try:
        digest = hashlib.blake2b(json.dumps(plan, sort_keys=True).encode(), digest_size=16).digest()
    except (TypeError, ValueError):
        digest = None
    if digest is not None and digest in _VALID_PLANS:
        return
    try:
        sections = plan["sections"]
        placements = [placement for section in sections for step in section["steps"] for placement in step]
        # The shell check only needs each step's length right; a one-item stand-in keeps it independent of the size.
        shrunk = {**plan, "sections": [{**section, "steps": [[None] if 1 <= len(step) <= step_limit else step
                                                            for step in section["steps"]]} for section in sections]}
        keys = [json.dumps(placement, sort_keys=True) for placement in placements]
    except (KeyError, TypeError, ValueError):
        digest = None
    else:
        if shell.is_valid(shrunk):
            fresh = {key: placement for key, placement in zip(keys, placements) if key not in _VALID_ENTRIES}
            if all(entry.is_valid(placement) for placement in fresh.values()):
                _remember(_VALID_ENTRIES, fresh)
                if digest is not None:
                    _remember(_VALID_PLANS, [digest])
                return
    full.validate(plan)      # not valid, or not shaped like a plan: let jsonschema report its first error


def load_plan(path):
    """Resolve relative plan includes once; retain each module's authorship."""
    loaded, sections, assets = set(), [], []
    root_plan = None

    def visit(path, active):
        nonlocal root_plan
        path = Path(path).resolve()
        if path in active:
            raise ValueError("Plan include cycle: " + " -> ".join(map(str, (*active,path))))
        if path in loaded:
            return
        if len(loaded) >= 200:
            raise ValueError("Plan exceeds 200 included files")
        loaded.add(path)
        plan = json.loads(path.read_text(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON {v}")))
        check_schema(plan)
        if root_plan is None:
            root_plan = plan
        for section in plan["sections"]:
            section = copy.deepcopy(section)
            section.setdefault("author",plan["author"])
            if plan.get("license"):
                section.setdefault("license",plan["license"])
            sections.append(section)
        for asset in plan.get("assets",[]):
            asset = str((path.parent / asset).resolve())
            if asset not in assets:
                assets.append(asset)
        for child in plan.get("includes",[]):
            visit(path.parent / child, (*active,path))
    visit(path, ())
    # Licences were copied to the sections owned by each declaring plan. Keeping
    # the root licence here would incorrectly apply it to unlicensed includes.
    result = {k:v for k,v in root_plan.items() if k not in {"includes","assets","sections","license"}}
    result.update(sections=sections, assets=assets)
    check_schema(result)
    return result


def frame(value):
    result = np.eye(4)
    result[:3, 3] = value["at"]
    result[:3, :3] = value.get("matrix", np.eye(3))
    a = result[:3, :3]
    if not np.isfinite(result).all() or not np.allclose(a.T @ a,np.eye(3),atol=1e-4,rtol=0) or np.linalg.det(a) < 0:
        raise ValueError("An anchor frame must use a finite proper rotation")
    return result


def repeated(entries):
    for entry in entries:
        if "repeat" not in entry:
            yield entry
            continue
        for i in range(entry["repeat"]["count"]):
            yield {**{k:v for k,v in entry.items() if k != "repeat"}, "id":f"{entry['id']}-{i}",
                   "at":(np.array(entry["at"]) + i*np.array(entry["repeat"]["step"])).tolist()}


def rotation(axis="y", degrees=0):
    """Active right-handed rotation on column vectors, in LDraw's coordinate frame."""
    if axis not in {"x", "y", "z"} or not math.isfinite(degrees):
        raise ValueError("Rotation needs axis x/y/z and finite degrees")
    a = math.radians(degrees)
    c, s = round(math.cos(a), 12), round(math.sin(a), 12)
    return Matrix({"x": [[1, 0, 0], [0, c, -s], [0, s, c]],
                   "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
                   "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis])


def serialize_mpd(model):
    """Always emit a main FILE block, UTF-8 without BOM, and specification CRLF."""
    text = model.to_ldraw()
    if not model.submodels:
        text = f"0 FILE {model.name}\n{text}\n0 NOFILE"
    return text.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"


def dependency_order(sections):
    """Build referenced modules before using their physical snap interfaces."""
    table = {normalized(s['name']): s for s in sections}
    ordered, done, active = [], set(), set()
    def visit(key):
        if key in active:
            raise ValueError(f'Plan submodel cycle at {key}')
        if key in done:
            return
        active.add(key)
        for step in table[key]['steps']:
            for entry in step:
                child = normalized(entry['ref'])
                if child in table:
                    visit(child)
        active.remove(key)
        done.add(key)
        ordered.append(table[key])
    for key in table:
        visit(key)
    return ordered


CULL_SNAP_PREVIEW = True     # False inspects the whole model for every snap: the slow reference the tests compare with


def snap_placement(entry, model, models, placed, parts, matrix, instance_limit, neighbourhood=None):
    from .connectivity import inspect_connections, snap_report
    from dataclasses import replace
    options = entry['snap']
    if options['to'] not in placed:
        raise ValueError('snap.to must identify an earlier placement in the same section')
    support, _ = placed[options['to']]
    moving = Piece.place(entry['ref'], colour=entry['colour'],
                         position=Vector(*options.get('near', jsonable(support.position))), matrix=matrix)
    if neighbourhood is None:
        objects = list(model.objects)
        submodels = {normalized(m.name): m for m in models.values() if m is not model}
    else:   # only what is near the fixed part is inspected (see snapindex); the answer is the same, the cost local
        objects, extra = neighbourhood.cull(model.objects, support, moving)
        submodels = neighbourhood.needed_submodels(model, [*objects, moving], extra)
    preview = replace(model, objects=[*objects, moving], submodels=submodels)
    candidate_index = options.get('candidate', 0)
    nearest = bool(options.get('nearest')) and 'candidate' not in options and 'near' in options
    report = None
    if nearest and neighbourhood is not None:
        # The editor's question is only "the connection nearest the drop": see fastsnap for when that can be
        # answered from a few candidates; None sends it down the full algorithm below.
        from .fastsnap import nearest as fast_nearest
        report = fast_nearest(preview, parts, moving, support, options['near'], options, instance_limit, neighbourhood)
    if report is None:
        inspection = inspect_connections(preview, parts, instance_limit)
        groups = [[o.index for o in inspection.occurrences if o.occurrence.path[0].piece is piece]
                  for piece in (moving, support)]
        try:
            moving_index = groups[0][options.get('moving_leaf', 0)]
            fixed_index = groups[1][options.get('fixed_leaf', 0)]
        except IndexError as exc:
            raise ValueError('snap leaf index is outside the selected part/submodel') from exc
        report = snap_report(preview, parts, moving_index, fixed_index, moving_depth=0,
                             limit=64 if nearest else candidate_index+1, instance_limit=instance_limit,
                             moving_feature=options.get('moving_feature'), fixed_feature=options.get('fixed_feature'),
                             inspection=inspection)
    if candidate_index >= len(report['candidates']):
        raise ValueError(f"snap {entry['id']}: no candidate {candidate_index}; inspect connector coverage/occupancy")
    candidates = report['candidates']
    if nearest:
        # The toolkit's ranking is not about where the part was dropped; with `nearest` the connection closest to
        # `near` wins (a brick dropped on top of a plate stays on top instead of snapping to its underside).
        usable = [c for c in candidates if c['collision']['status'] != 'blocked'] or candidates
        near = options['near']
        candidate = min(usable, key=lambda c: sum((a - b) ** 2 for a, b in zip(c['local_placement']['at'], near)))
    else:
        candidate = candidates[candidate_index]
    if candidate['collision']['status'] == 'blocked':
        raise ValueError(f"snap {entry['id']}: candidate collides with a rectangular body")
    local = candidate['local_placement']
    return Vector(*local['at']), Matrix(local['matrix'])


def _section_closure(sections, start):
    """The sections `start` needs (itself and every submodel it references, however deep)."""
    table = {normalized(s['name']): s for s in sections}
    needed, stack = [], [normalized(start)]
    while stack:
        key = stack.pop()
        if key in needed or key not in table:
            continue
        needed.append(key)
        for step in table[key]['steps']:
            stack.extend(normalized(entry['ref']) for entry in step)
    return [table[k] for k in table if k in needed]


def build_plan(plan, parts, *, instance_limit=100000, until=None, validate=True):
    """Build the plan into MPD text: (text, parsed model, diagnostics).

    `until=(section name, entry id)` builds only what that placement depends on and returns its resolved
    `(position, matrix)` as soon as it is placed (the snap probe: nothing after it matters, and nothing
    is serialised or validated). `validate=False` skips the final validation pass: (text, None, []).
    """
    check_schema(plan)
    from .catalog import resolve_plan
    plan = resolve_plan(plan, parts)
    if plan.get("includes"):
        raise ValueError("Resolve includes with load_plan(path) before build_plan")
    names = [s["name"] for s in plan["sections"]]
    if len(set(map(normalized, names))) != len(names):
        raise ValueError("Section names must be unique ignoring case")
    models = {s["name"]: Model(name=s["name"]) for s in plan["sections"]}
    from .document import parse_source, section_table
    for asset in plan.get("assets", []):
        for sub in section_table(parse_source(asset)).values():
            if normalized(sub.name) in {normalized(n) for n in models}:
                raise ValueError(f"Duplicate section {sub.name}; extract assets with unique namespaces")
            from dataclasses import replace
            models[sub.name] = replace(sub, submodels={})
    known = set(map(normalized, models))
    anchors = {normalized(s["name"]): {name:frame(value) for name,value in s.get("anchors",{}).items()} for s in plan["sections"]}
    regular = profiles()
    from .snapindex import Neighbourhood
    neighbourhood = Neighbourhood(models, parts) if CULL_SNAP_PREVIEW else None
    sections = plan["sections"] if until is None else _section_closure(plan["sections"], until[0])
    for section in dependency_order(sections):
        model = models[section["name"]]
        model.set_header(description=section["description"], name=model.name, author=section.get("author",plan["author"]), ldraw_org="Model")
        if section.get("license",plan.get("license")):
            model.set_header(license=section.get("license",plan.get("license")))
        placed = {}
        for step_index, step in enumerate(section["steps"]):
            if step_index:
                model.add_step()
            for entry in repeated(step):
                if len(placed) >= instance_limit:
                    raise ValueError(f"More than {instance_limit} direct placements in a section")
                if entry["id"] in placed:
                    raise ValueError(f"Duplicate placement id {entry['id']} in {model.name}")
                ref = entry["ref"]
                code = ref.casefold().removesuffix(".dat")
                if normalized(ref) not in known and parts.find_part(code=code) is None:
                    raise ValueError(f"Unknown reference {ref}; search/inspect the library first")
                if entry["colour"] not in parts.colours_by_code or entry["colour"] == 24:
                    raise ValueError(f"Unknown or unsuitable placement colour {entry['colour']}")
                matrix = Matrix(entry["matrix"]) if "matrix" in entry else rotation("y", entry.get("yaw", 0))
                if "snap" in entry:
                    position, matrix = snap_placement(entry, model, models, placed, parts, matrix, instance_limit,
                                                      neighbourhood)
                elif "on" in entry:
                    support_id = entry["on"]
                    if support_id not in placed:
                        raise ValueError(f"on: {support_id} must refer to an earlier placement in the same section")
                    support, support_code = placed[support_id]
                    if (code not in regular or support_code not in regular
                            or not regular[code]['studs'] or not regular[support_code]['studs']):
                        raise ValueError("on placement only supports curated rectangular studded bricks/plates; use part inspection and explicit at for other parts")
                    if not np.allclose(np.array(support.matrix.rows)[:, 1], [0, 1, 0], atol=1e-6) or not np.allclose(np.array(matrix.rows)[:, 1], [0, 1, 0], atol=1e-6):
                        raise ValueError("on requires upright parts")
                    offset = entry.get("offset_studs", [0, 0])
                    delta = support.matrix * Vector(offset[0] * 20, -regular[code]["height"], offset[1] * 20)
                    position = support.position + delta
                    def stud_centres(profile, pose, orient):
                        return [pose + orient * Vector(20 * (x - (profile["x_studs"]-1)/2), 0, 20 * (z - (profile["z_studs"]-1)/2))
                                for x in range(profile["x_studs"]) for z in range(profile["z_studs"])]
                    lower = stud_centres(regular[support_code], support.position, support.matrix)
                    upper = stud_centres(regular[code], position, matrix)
                    if not any(abs(a.x-b.x) < 1e-5 and abs(a.z-b.z) < 1e-5 for a in lower for b in upper):
                        raise ValueError(f"on: {entry['id']} has no aligned stud/socket with {support_id}; check half-stud offsets and part axes")
                elif "attach" in entry:
                    attach = entry["attach"]
                    if attach["to"] not in placed:
                        raise ValueError("attach.to must identify an earlier placement in the same section")
                    support, _ = placed[attach["to"]]
                    try:
                        fixed_frame = anchors[normalized(support.reference)][attach["anchor"]]
                        moving_frame = anchors[normalized(ref)][attach["using"]]
                    except KeyError as exc:
                        raise ValueError(f"Unknown module anchor {exc.args[0]}; declare both interfaces in the plans") from exc
                    support_frame = frame(dict(at=[support.position.x,support.position.y,support.position.z],matrix=support.matrix.rows))
                    offset = frame(dict(at=attach.get("offset",[0,0,0])))
                    transform = support_frame @ fixed_frame @ offset @ np.linalg.inv(moving_frame)
                    position = Vector(*transform[:3,3])
                    matrix = Matrix(transform[:3,:3].tolist())
                else:
                    position = Vector(*entry["at"])
                model.add(Comment(f"// {entry['id']}: {entry.get('purpose', ref)}"))
                piece = Piece.place(ref, colour=entry["colour"], position=position, matrix=matrix)
                model.add(piece)
                placed[entry["id"]] = (piece, code)
                if until is not None and entry["id"] == until[1] and normalized(section["name"]) == normalized(until[0]):
                    return piece.position, piece.matrix
    if until is not None:
        raise ValueError(f"{until[1]} is not placed in {until[0]}")
    root = models[names[0]]
    # MPD namespace is shared across all sections; serializer emits each section once.
    root.submodels = {normalized(m.name): m for m in list(models.values())[1:]}
    text = serialize_mpd(root)
    if not validate:
        return text, None, []
    parsed, diagnostics = validate_text(text, parts, name=names[0], instance_limit=instance_limit)
    if any(d["severity"] == "error" for d in diagnostics):
        return text, parsed, diagnostics
    return text, parsed, diagnostics
