"""The fast-path decision engine: request text -> generator parameters, an edit, a clarifying question or "ask first".

    understand (rules shortcut, else ONE cheap structured call)  ->  route a spec.v1 to a generator  ->  validate and clamp

Nothing here builds. The result tells the caller (the build page) which deterministic generator to run with which
parameters, or that the paid AI build is needed, together with an estimate so the person can be asked before any
money is spent. Parameters are checked against the generators' JSON schemas (generator_bridge) and out-of-range values
are clamped with a note, the way the generators do it themselves. `rules_guess()` is the no-LLM, millisecond-fast
variant the composer calls while typing.
"""
from __future__ import annotations

import asyncio
import copy
import re
import statistics
import time
from typing import Any, Optional

import generator_bridge
import request_log
import settings
import understand
import usage
import chat_routing
from store import get_store

# Seconds a generator takes in the resident worker (build + checks + preview), from the proposal's measurements.
SECONDS = {"house": 15, "building": 15, "starfighter": 20, "arcade-bay": 10, "street-lantern": 10}
DEFAULT_SECONDS = 20
DEFAULT_ESTIMATE = {"eur_low": 1.0, "eur_high": 2.0, "minutes_low": 15, "minutes_high": 30}   # proposal: EUR 1.5-2, 24-35 min
MIN_HISTORY = 3
HISTORY_TTL_S = 60
# Families a generator exists for when the registry is not loaded yet (the estimate endpoint never waits for it).
STATIC_FAMILIES = {"car", "van", "truck", "motorcycle", "boat", "aircraft", "spaceship", "house", "building", "train"}
GENERIC_KINDS = {"model", "vehicle", "building", "thing", "object", "toy"}
SIZE_PARAMS = ("length", "wingspan", "width", "depth", "trunk_courses")
WHOLE = ("whole", "model", "it", "", "size")

ROLE_PARAMS = {"body": ["body", "wall", "hull"], "hull": ["hull", "body", "wall"], "walls": ["wall", "body"],
               "wheels": ["wheels", "wheel"], "glass": ["glass", "canopy"], "canopy": ["canopy", "glass"],
               "roof": ["roof"], "trim": ["trim"], "accent": ["accent"], "door": ["door"], "wing": ["wing"],
               "chimney": ["chimney"], "engine": ["engine"], "foliage": ["foliage"], "trunk": ["trunk"], "flowers": ["flower"]}
FEATURE_WORDS = {"door": "door", "window": "windows", "windows": "windows", "porch": "porch", "chimney": "chimney",
                 "dormer": "dormer", "engine": "engines", "engines": "engines", "wheel": "wheels", "wheels": "wheels",
                 "wagon": "wagons", "wagons": "wagons", "carriage": "wagons", "carriages": "wagons"}
# scale word -> share of the way from the default to the maximum (negative: from the default to the minimum)
SCALE_SHARE = {"micro": -1.0, "mini": -0.5, "small": 0.0, "medium": 0.3, "large": 0.7}
BIG_CHANGE = 0.30          # a size change above this share of the current value replaces the model rather than tweaks it
_DROP = object()


# ---------------------------------------------------------------- registry access

def _entries() -> list[dict]:
    return (generator_bridge.cached() or {}).get("entries") or []


def _entry(generator_id: str) -> Optional[dict]:
    return next((e for e in _entries() if e["id"] == generator_id), None)


def _norm_colour(name: Any) -> Any:
    """A colour the generators accept (a code, or a lower case name), or None. Rebrickable spellings are folded onto them."""
    if isinstance(name, int) and not isinstance(name, bool):
        return name
    names = set((generator_bridge.cached() or {}).get("colours") or [])
    text = re.sub(r"[\s_-]+", " ", str(name)).strip().casefold().replace("gray", "grey")
    return next((c for c in (text, text.removeprefix("flat "), text.removeprefix("bright ")) if c in names), None)


# ---------------------------------------------------------------- parameters: clamp and validate

def clamp(entry: dict, params: Optional[dict]) -> tuple[dict, list[str]]:
    """Params with defaults filled, every value made valid for the generator's schema, and a note per adjustment."""
    schema = entry["params"]["properties"]
    defaults = entry.get("defaults") or {}
    out, notes = dict(defaults), []
    for key, value in (params or {}).items():
        if key not in schema:
            notes.append(f"{key} is not a parameter of {entry['id']}; ignored")
        elif value is not None:
            fixed = _clamp_value(key, schema[key], value, defaults.get(key), notes)
            if fixed is not _DROP:
                out[key] = fixed
    return out, notes


def _clamp_value(key: str, spec: dict, value: Any, default: Any, notes: list[str]) -> Any:
    fallback = _DROP if default is None else default
    if spec.get("x-colour"):
        known = _norm_colour(value)
        if known is None:
            notes.append(f"unknown colour {value!r} for {key}; " + ("kept the default" if default is not None else "ignored"))
            return fallback
        return known
    if "enum" in spec:
        match = next((x for x in spec["enum"] if x == value or str(x).casefold() == str(value).casefold()), _DROP)
        if match is _DROP:
            notes.append(f"{key} {value!r} is not one of {', '.join(map(str, spec['enum']))}; using {default!r}")
            return fallback
        return match
    kind = spec.get("type")
    if kind in ("integer", "number"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            notes.append(f"{key} {value!r} is not a number; using {default!r}")
            return fallback
        if kind == "integer":
            number = int(round(number))
            if spec.get("x-even"):
                number += number % 2
        low, high = spec.get("minimum"), spec.get("maximum")
        fixed = min(high, max(low, number)) if low is not None and high is not None else number
        if fixed != value:
            notes.append(f"{key} {value} adjusted to {fixed} ({low}-{high}" + (", even)" if spec.get("x-even") else ")"))
        return fixed
    if kind == "boolean":
        return bool(value)
    if kind == "array":
        items = value if isinstance(value, list) else [value]
        allowed = spec.get("items", {}).get("enum")
        kept = list(dict.fromkeys(x for x in items if allowed is None or x in allowed))
        if len(kept) != len(items):
            notes.append(f"{key}: some values were not valid and were dropped")
        return kept
    return value


# ---------------------------------------------------------------- choosing the generator

def _word_in(needle: str, text: str) -> bool:
    """`needle` as a whole word (or phrase, plain plural allowed) inside `text`: "house" is in "red house", not in "lighthouse"."""
    return bool(re.search(rf"(?<![a-z]){re.escape(needle)}(?:e?s)?(?![a-z])", text))


def _kind_hit(entry: dict, kind: str) -> bool:
    kind = kind.casefold().strip()
    return bool(kind) and any(kind == k or _word_in(k, kind) or _word_in(kind, k) for k in entry["kinds"])


def choose(spec: dict) -> tuple[Optional[dict], str]:
    """The generator entry for a new-model spec, with a reason when there is none."""
    subject = spec.get("subject") or {}
    family, kind = subject.get("family") or "other", (subject.get("kind") or "").strip()
    if not _entries():
        return None, "the generator registry is not available"
    rows = []
    for position, entry in enumerate(_entries()):
        by_kind, by_family = _kind_hit(entry, kind), family in entry["families"]
        if (entry["quality"].startswith("Small accent piece") and not by_kind) or not (by_kind or by_family):
            continue
        exact = kind.casefold() in entry["kinds"]
        template = entry.get("kind") == "template"         # a model turned into a template (docs/plans/active/build-templates)
        rows.append((-(by_kind * 4 + by_family * 2 + (entry["families"][0] == family) + exact), template,
                     not entry.get("presets"), len(entry["families"]), position, entry))
    if not rows:
        return None, f"no generator builds a {kind or family}"
    # best score, then a generator before a template (it is parametric from scratch and tested at its corners; a template
    # wins only a kind no generator matches as well, "flatbed truck"), then the more specific entry, then registry order
    # (the building grammar, which carries presets, before the older flat generator that answers the same words)
    best = min(rows, key=lambda r: r[:5])[5]
    generic = not kind or kind.casefold() in GENERIC_KINDS or kind.casefold() == family
    if not generic and not _kind_hit(best, kind):
        return None, f"no generator builds a {kind}"
    return best, ""


def _feature_param(entry: Optional[dict], name: str) -> Optional[str]:
    for word in reversed(re.sub(r"[^a-z ]", " ", name.casefold()).split()):        # "sloped roof" -> roof, "two windows" -> windows
        if word in FEATURE_WORDS:
            param = FEATURE_WORDS[word]
            return param if entry is None or param == "wheels" or param in entry["params"]["properties"] or param == "door" and "door_side" in entry["params"]["properties"] else None
    return None


def _unsupported_reason(spec: dict) -> str:
    if spec.get("reference_hint"):
        return f"{spec['reference_hint']} is a named real model, which the fast path cannot build"
    if (spec.get("constraints") or {}).get("parts_mode") in ("prefer", "only"):
        return "limiting the build to your own parts needs the AI builder"
    for feature in spec.get("features") or []:
        if feature.get("required") and not _feature_param(None, feature.get("name", "")):
            return f"a required feature ({feature.get('name')}) is beyond the generators"
    return ""


# House-dialect settings of the building grammar and where they live in its configuration (to show "from" values)
ALIAS_PATHS = {"width": ("footprint", "width"), "depth": ("footprint", "depth"), "roof_colour": ("roof", "colour"),
               "wall_colour": ("walls", "colour"), "trim_colour": ("storeys", "band_colour"),
               "door_colour": ("openings", "door_colour"), "glass_colour": ("openings", "glass_colour"),
               "chimney_colour": ("details", "chimney_colour"), "chimney": ("details", "chimney"),
               "porch": ("details", "porch")}


# House-dialect settings that a grammar patch to these configuration fields overrides (the grammar lets the flat
# value win over the group, so the flat one must go when the person addresses the field directly)
ALIAS_SHADOWS = {"width": {("footprint", "width")}, "depth": {("footprint", "depth")},
                 "courses": {("storeys", "count"), ("storeys", "courses")}, "door_side": {("openings", "door"), ("openings", "door_position")},
                 "door_style": {("openings", "door")}, "windows": {("openings", "count"), ("openings", "ground_windows"), ("openings", "upper_windows")},
                 "window_sides": {("openings", "sides")}, "roof_style": {("roof", "style")}, "roof_tiers": {("roof", "tiers")},
                 "wall_colour": {("walls", "colour")}, "roof_colour": {("roof", "colour")}, "door_colour": {("openings", "door_colour")},
                 "glass_colour": {("openings", "glass_colour")}, "chimney_colour": {("details", "chimney_colour")},
                 "trim_colour": {("openings", "frame_colour"), ("storeys", "band_colour")},
                 "porch": {("details", "porch")}, "chimney": {("details", "chimney")}}


def _effective(params: dict, key: str, default: Any = None) -> Any:
    """A setting's value: the flat param, else (grammar builds) the value inside the nested configuration."""
    if params.get(key) is not None:
        return params[key]
    group, name = ALIAS_PATHS.get(key, (None, None))
    value = (params.get(group) or {}).get(name) if group else None
    return default if value is None else value


# ---------------------------------------------------------------- spec -> params

def _size_params(entry: dict, spec: dict, notes: list[str], base: Optional[dict] = None) -> dict:
    schema, out = entry["params"]["properties"], {}
    size = spec.get("size") or {}
    studs = size.get("studs") or {}
    if size.get("unit_ambiguous"):
        notes.append("read the size as studs (not bricks)")
    for field, names in (("width", ("width", "wingspan")), ("depth", ("depth",)), ("length", ("length", "depth"))):
        if studs.get(field) is not None:
            param = next((n for n in names if n in schema and n not in out), None)
            if param is None:
                notes.append(f"{field} is fixed for the {entry['id']} design")
            else:
                out[param] = studs[field]
    if studs.get("height_courses") is not None:
        if "courses" in schema:
            out["courses"] = studs["height_courses"]
        else:
            notes.append("height is fixed for this design")
    if size.get("scale") and not studs:
        share = SCALE_SHARE[size["scale"]]
        for name in SIZE_PARAMS:
            if name in schema and "minimum" in schema[name]:
                low, high = schema[name]["minimum"], schema[name]["maximum"]
                default = _effective(base or {}, name, schema[name].get("default", low))
                out[name] = round(default + (default - low) * share) if share < 0 else round(default + (high - default) * share)
    return out


def _colour_param(entry: dict, role: str) -> Optional[str]:
    schema = entry["params"]["properties"]
    for alias in ROLE_PARAMS.get(role, [role]):
        for name in (alias, f"{alias}_colour", f"colour_{alias}"):      # colour_<role>: templates
            if schema.get(name, {}).get("x-colour"):
                return name
        if alias in ("body", "wall", "hull") and "colour" in schema:
            return "colour"
    return None


def _feature_params(entry: dict, spec: dict, notes: list[str]) -> dict:
    schema, out = entry["params"]["properties"], {}
    features = spec.get("features") or []
    for feature in features:
        name, count = feature.get("name", ""), feature.get("count")
        param = _feature_param(entry, name)
        if param == "wheels":
            continue                                                        # every vehicle has its wheels
        if param is None:
            notes.append(f"{name} is not supported by the {entry['id']} design; left out")
        elif param == "windows":
            out["windows"] = count or 2
        elif param == "door":
            default = schema["door_side"].get("default", "left")
            out["door_side"] = default if default != "none" else "left"
        elif param == "chimney":
            out["chimney"] = "right"
        elif param == "engines":
            out["engines"] = count or 2
        elif param == "wagons":
            out["wagons"] = count if count is not None else 2
        else:
            out[param] = True
    if "roof_style" in schema and "flat" in " ".join(f.get("name", "") for f in features).casefold():
        out["roof_style"] = "flat"
    return out


def pick_preset(entry: dict, kind: str, family: str = "") -> Optional[str]:
    """The preset of a grammar entry a request means: its name or keyword as a whole word ("a lighthouse", "a warehouse"),
    else the plain house for a generic house/building request. None for entries without presets."""
    presets = entry.get("presets") or {}
    kind = (kind or "").casefold().strip().replace("-", " ")
    if not presets:
        return None
    named = next((k for k in presets if kind and k.replace("-", " ") == kind), None)
    if named:
        return named
    for name, preset in presets.items():
        if kind and any(kind == w or _word_in(w, kind) for w in preset.get("keywords") or []):
            return name
    return "house" if "house" in presets else next(iter(presets))


# Wall colour -> (roof, trim): a roof that reads against the walls, trim in white or light grey. Small on purpose.
HARMONY = {"red": ("dark grey", "white"), "blue": ("dark blue", "white"), "green": ("dark green", "white"),
           "yellow": ("dark red", "white"), "orange": ("dark red", "white"), "white": ("dark red", "light grey"),
           "tan": ("dark tan", "white"), "brown": ("dark grey", "tan"), "reddish brown": ("dark grey", "tan"),
           "black": ("dark grey", "white"), "grey": ("dark red", "white"), "light grey": ("dark red", "white"),
           "dark grey": ("dark red", "white"), "pink": ("dark pink", "white"), "purple": ("dark grey", "white")}
WALL_ROLES = ("body", "wall", "walls")


def _harmony(entry: dict, wanted: dict, notes: list[str]) -> None:
    """One named colour for a building ("a blue house"): pick the roof and trim to go with it instead of the default red."""
    schema = entry["params"]["properties"]
    wall = next((p for p in ("wall_colour", "colour") if p in wanted), None)
    if wall is None or "roof_colour" not in schema or any(p in wanted for p in ("roof_colour", "trim_colour")):
        return
    name = str(_norm_colour(wanted[wall]) or wanted[wall]).casefold()
    base = re.sub(r"^(dark|light|bright|medium)\s+", "", name)
    roof, trim = HARMONY.get(name) or HARMONY.get(base) or ("dark grey", "white")
    if _norm_colour(roof) == _norm_colour(name):
        roof = "dark grey" if name != "dark grey" else "dark red"
    wanted["roof_colour"] = roof
    if "trim_colour" in schema:
        wanted["trim_colour"] = trim
    notes.append(f"chose a {roof} roof" + (f" and {trim} trim" if "trim_colour" in schema else "") + f" to go with the {name} walls")


def params_from_spec(entry: dict, spec: dict) -> tuple[dict, list[str]]:
    """Generator params for a new-model spec, and the notes (what was assumed, left out or clamped)."""
    notes: list[str] = []
    subject = spec.get("subject") or {}
    preset = pick_preset(entry, subject.get("kind") or "", subject.get("family") or "")
    base = copy.deepcopy(entry["presets"][preset]["config"]) if preset else {}
    wanted = _size_params(entry, spec, notes, base)
    named = {}
    for role, name in (spec.get("colours") or {}).items():
        param = _colour_param(entry, role)
        if param is None:
            notes.append(f"a {role} colour is not a setting of the {entry['id']} design; left out")
        else:
            wanted[param] = name
            named[role] = name
    if len(named) == 1 and next(iter(named)) in WALL_ROLES:
        _harmony(entry, wanted, notes)
    wanted.update(_feature_params(entry, spec, notes))
    params, more = clamp(entry, {**base, **wanted})
    return params, notes + more


def spec_from_params(entry: dict, params: dict) -> dict:
    """A small spec.v1 describing the current build, so the model reads a follow-up against it."""
    colours = {}
    for role in ("body", "roof", "trim", "wheels", "glass", "accent", "door", "wing", "chimney"):
        name = _colour_param(entry, role)
        if name and _effective(params, name) is not None:
            colours.setdefault(role, str(_effective(params, name)))
    studs = {k: _effective(params, k) for k in ("width", "depth", "length") if _effective(params, k) is not None}
    spec: dict = {"intent": "new_model", "subject": {"family": entry["families"][0], "kind": entry["kinds"][0]}}
    if colours:
        spec["colours"] = colours
    if studs:
        spec["size"] = {"studs": studs}
    return spec


# ---------------------------------------------------------------- edits

def edit_params(entry: dict, current: dict, edits: list[dict]) -> tuple[dict, list[dict], list[str], Optional[str]]:
    """(new params, changes, notes, history action) for the edits that map to generator parameters."""
    schema = entry["params"]["properties"]
    wanted, notes, history = dict(current), [], None
    for edit in edits:
        op, target = edit.get("op"), str(edit.get("target", "")).casefold().strip()
        if op in ("undo", "revert"):
            history = op
        elif op == "set_colour":
            param = _colour_param(entry, "body" if target in WHOLE else target)
            if param is None:
                notes.append(f"the {target} colour is not a setting of this design")
            else:
                wanted[param] = edit.get("value")
        elif op == "set_param":
            wanted_key = re.sub(r"[\s-]+", "_", target)
            key = next((k for k in schema if k == wanted_key), None) or next((k for k in schema if wanted_key and k.startswith(wanted_key)), None)
            if key is None:
                notes.append(f"{target} is not a setting of this design")
            else:
                wanted[key] = edit.get("value")
        elif op == "scale_param":
            if not _scale(schema, wanted, edit, target):
                notes.append(f"cannot change the {target} of this design")
        elif op in ("add_feature", "remove_feature"):
            if not _toggle(schema, wanted, edit, target, op == "add_feature", entry):
                notes.append(f"cannot {op.split('_')[0]} {target} on this design")
        else:
            notes.append(f"{op} {target} needs the AI builder")
    params, more = clamp(entry, wanted)
    changes = [{"param": k, "label": k.replace("_", " "), "from": _effective(current, k), "to": params[k]}
               for k in params if params[k] != _effective(current, k, current.get(k)) and not isinstance(params[k], dict)]
    return params, changes, notes + more, history


def _scale(schema: dict, wanted: dict, edit: dict, target: str) -> bool:
    delta = str(edit.get("delta", "")).casefold()
    sign = -1 if delta in ("smaller", "shorter", "lower", "narrower") else 1
    amount, unit = edit.get("amount"), edit.get("unit")
    if target == "roof" and "roof_tiers" in schema:
        names = ["roof_tiers"]
    elif (unit == "courses" or delta in ("taller", "lower")) and ("courses" in schema or "trunk_courses" in schema):
        names = ["courses" if "courses" in schema else "trunk_courses"]
    elif delta in ("longer", "shorter"):
        names = [n for n in ("length", "depth") if n in schema][:1]
    elif delta in ("wider", "narrower"):
        names = [n for n in ("width", "wingspan") if n in schema][:1]
    elif target in WHOLE:
        names = [n for n in SIZE_PARAMS if n in schema]
    else:
        return False
    for name in names:
        if amount is None:
            step, unit_used = (1, "courses") if schema[name].get("x-even") is None else (2, "studs")
        else:
            step, unit_used = amount, unit
        base = float(wanted.get(name, schema[name].get("default", 0)))
        wanted[name] = base * (1 + sign * step / 100) if unit_used == "percent" else base + sign * step
    return bool(names)


def _toggle(schema: dict, wanted: dict, edit: dict, target: str, add: bool, entry: dict) -> bool:
    param, count = _feature_param(entry, target), edit.get("count")
    if param == "windows":
        wanted["windows"] = max(0, int(wanted.get("windows", 0)) + (count or 1) * (1 if add else -1))
    elif param == "door":
        wanted["door_side"] = "left" if add else "none"
    elif param == "chimney":
        wanted["chimney"] = (edit.get("where") if edit.get("where") in ("left", "right") else "right") if add else "none"
    elif param in ("porch", "dormer"):
        wanted[param] = add
    elif param == "engines":
        wanted["engines"] = count or (2 if add else 1)
    elif param == "wagons":
        wanted["wagons"] = max(0, int(wanted.get("wagons", 0)) + (count or 1) * (1 if add else -1))
    else:
        return False
    return True


# ---------------------------------------------------------------- role edits: a build with no live generator source

def _model_spec() -> dict:
    """Stand-in for CURRENT_SPEC on a build that has parts but no generator to read settings from."""
    return {"intent": "new_model", "subject": {"family": "other"}, "note": understand.STAND_IN_NOTE}


def _route_role_edit(spec: dict, context: dict, confidence: float) -> dict:
    """The edits of a spec as role edits (recolour / remove by role or selection) for the build's own plan."""
    edits, notes, history, shown = [], [], None, []
    for edit in spec.get("edits") or []:
        op, target = edit.get("op"), str(edit.get("target", "")).casefold().strip()
        if op in ("undo", "revert"):
            history = op
        elif op == "set_colour":
            colour = _norm_colour(edit.get("value"))
            if colour is None:
                return {"route": "clarify", "question": f"I don't know the colour {edit.get('value')!r}. Which colour would you like?",
                        "choices": ["red", "blue", "white", "dark grey"]}
            role = None if target in understand.SELECTION_WORDS or target in WHOLE else target
            edits.append({"op": "recolour", "role": role, "colour": colour})
            shown.append({"label": f"{role or 'selection'} colour", "from": None, "to": colour})
        elif op == "remove_feature":
            role = None if target in understand.SELECTION_WORDS else target
            edits.append({"op": "remove", "role": role})
            shown.append({"label": role or "selection", "from": None, "to": "removed"})
        else:
            notes.append(f"{op} {target} needs the AI builder".strip())
    if not edits and not history:
        return _needs_ai(spec, "; ".join(notes[:2]) or "that is more than a recolour or a removal")
    out = {"route": "edit", "role_edits": edits, "changes": shown, "notes": notes, "confidence": round(confidence, 2),
           "cost_eur": 0.0, "seconds_estimate": 1}
    if history:
        out["history"] = history
    return out


# scale/direction words that continue ("again", "bigger") or soften ("a bit less") the last numeric change
RELATIVE = {"again": (0, 1), "do it again": (0, 1), "once more": (0, 1), "more": (0, 1), "even more": (0, 1), "a bit more": (0, .5),
            "a little more": (0, .5), "some more": (0, 1), "bigger": (1, 1), "larger": (1, 1), "even bigger": (1, 1), "a bit bigger": (1, .5),
            "a little bigger": (1, .5), "longer": (1, 1), "wider": (1, 1), "taller": (1, 1), "smaller": (-1, 1), "shorter": (-1, 1),
            "narrower": (-1, 1), "a bit smaller": (-1, .5), "a bit less": (-2, .5), "a little less": (-2, .5), "less": (-2, .5),
            "not so much": (-2, .5), "too much": (-2, .5), "much less": (-2, 1)}


def relative_spec(text: str, last: Optional[dict]) -> Optional[dict]:
    """"again", "a bit less", "bigger" read against the last numeric change of the build (free, no model).

    last = {"changes": [{"param", "from", "to"}]}. direction 0 repeats the last move, +1/-1 force up/down by its size,
    -2 takes half of it back.
    """
    found = RELATIVE.get(understand._key(text))
    if not found or not last:
        return None
    direction, factor = found
    edits = []
    for change in last.get("changes") or []:
        old, new, param = change.get("from"), change.get("to"), change.get("param")
        if not param or isinstance(old, bool) or isinstance(new, bool) or not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or old == new:
            continue
        step, up = max(1, round(abs(new - old) * factor)), new > old
        sign = (1 if up else -1) if direction == 0 else (-(1 if up else -1) if direction == -2 else direction)
        edits.append({"op": "set_param", "target": param, "value": new + sign * step})
    return {"intent": "edit", "subject": {"family": "other"}, "edits": edits, "confidence": 1.0} if edits else None


# ---------------------------------------------------------------- the building grammar's own text addressing

def _leaves(patch: dict, prefix: tuple = ()) -> list[tuple[tuple, Any]]:
    out = []
    for key, value in patch.items():
        out += _leaves(value, prefix + (key,)) if isinstance(value, dict) else [(prefix + (key,), value)]
    return out


def _dig(config: dict, path: tuple) -> Any:
    for key in path:
        config = config.get(key) if isinstance(config, dict) else None
    return config


def grammar_edit(entry: dict, current: dict, patch: dict, confidence: float = 1.0) -> dict:
    """An edit decision for a grammar config patch (what nl.apply_text understood) applied over the building's params."""
    params = copy.deepcopy(current.get("params") or {})
    leaves = _leaves(patch)
    touched = {path[:2] for path, _ in leaves if len(path) > 1}
    for alias, paths in ALIAS_SHADOWS.items():
        if alias in params and paths & touched:
            del params[alias]
    changes = []
    for path, value in leaves:
        group = params
        for key in path[:-1]:
            group = group.setdefault(key, {})
        before = group.get(path[-1])
        group[path[-1]] = value
        if before != value:
            changes.append({"param": ".".join(path), "label": " ".join(path).replace("_", " "), "from": before, "to": value})
    params, notes = clamp(entry, params)
    return {"route": "edit", "generator": entry["id"], "params": params, "changes": changes, "notes": notes,
            "confidence": round(confidence, 2), "cost_eur": 0.0, "seconds_estimate": SECONDS.get(entry["id"], DEFAULT_SECONDS)}


async def grammar_decision(text: str, current: Optional[dict]) -> Optional[dict]:
    """The building grammar's text addressing as a fast-path answer, or None when it does not read the WHOLE message
    (a partly understood request goes on to the understanding step, so nothing is applied half-way)."""
    entry = _entry(current["generator"]) if current else None
    if entry is None or entry["id"] != "building" or len(text) > 240 or text.rstrip().endswith("?"):
        return None
    nested = {k: v for k, v in (current.get("params") or {}).items() if isinstance(v, dict)}
    found = await generator_bridge.apply_text(nested, text)
    if not found or not found.get("understood") or found.get("missed"):
        return None
    patch: dict = {}
    for clause in found["understood"]:
        patch = _merge(patch, clause["patch"])
    decision = grammar_edit(entry, current, patch)
    return decision if decision["changes"] else None


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


# ---------------------------------------------------------------- history: the cost and time of real AI builds

_history: dict = {"at": -1e9, "stats": None}


def history_stats(force: bool = False) -> Optional[dict]:
    """Median and quartiles of the latest turn's cost (EUR) and duration of finished chats. None below MIN_HISTORY chats.

    Same population as the composer's step estimate (BuildHeader.estimateSteps): finished chats, latest turn.
    Read from the chat files at most once a minute.
    """
    now = time.monotonic()
    if not force and now - _history["at"] < HISTORY_TTL_S:
        return _history["stats"]
    import run_state
    store, costs, minutes = get_store(), [], []
    for chat in store.list_chats():
        if run_state.is_running(chat["id"]):
            continue
        stats = usage.turn_stats(store.messages(chat["id"]))
        if stats["turn_steps"] and stats["request_cost_eur"] > 0 and stats["duration_s"]:
            costs.append(stats["request_cost_eur"])
            minutes.append(stats["duration_s"] / 60)
    _history.update(at=now, stats=summarise(costs, minutes))
    return _history["stats"]


def _quartiles(values: list[float]) -> list[float]:
    return statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else [values[0]] * 3


def summarise(costs: list[float], minutes: list[float]) -> Optional[dict]:
    if len(costs) < MIN_HISTORY:
        return None
    (c1, c2, c3), (m1, m2, m3) = _quartiles(costs), _quartiles(minutes)
    return {"chats": len(costs), "eur_low": round(c1, 2), "eur_median": round(c2, 2), "eur_high": round(c3, 2),
            "minutes_low": round(m1), "minutes_median": round(m2), "minutes_high": round(m3)}


def ai_estimate() -> dict:
    stats = history_stats()
    if stats is None:
        return {**DEFAULT_ESTIMATE, "basis": "default"}
    return {**{k: stats[k] for k in DEFAULT_ESTIMATE}, "eur_median": stats["eur_median"], "minutes_median": stats["minutes_median"],
            "basis": f"quartiles of {stats['chats']} finished chats"}


# ---------------------------------------------------------------- routing

def _clarify(spec: dict) -> dict:
    ambiguity = next((a for a in spec.get("ambiguities") or [] if a.get("question")), None) or {}
    asked = ambiguity.get("question")
    out = {"route": "clarify", **({} if asked else {"generic": True}),
           "question": asked or 'What would you like to build? For example "a small red car" or "a cottage 10 studs wide".'}
    if ambiguity.get("options"):
        out["choices"] = [str(o) for o in ambiguity["options"][:4]]
    return out


def _needs_ai(spec: dict, reason: str) -> dict:
    return {"route": "needs_ai", "spec": spec, "reason": reason, "estimate": ai_estimate()}


def _generator_route(entry: dict, params: dict, notes: list[str], confidence: float) -> dict:
    return {"route": "generator", "generator": entry["id"], "title": entry["title"], "params": params, "notes": notes,
            "confidence": round(confidence, 2), "cost_eur": 0.0, "seconds_estimate": SECONDS.get(entry["id"], DEFAULT_SECONDS)}


def route(spec: dict, current: Optional[dict] = None, confidence: float = 0.7, context: Optional[dict] = None) -> dict:
    """The decision for one understood spec (no LLM; only the cached registry and the cached chat history).

    `context` describes the build (see workspace_api.build_context): without a live generator source an edit becomes a
    role edit of the build's own plan."""
    confidence = spec.get("confidence", confidence)
    intent = spec.get("intent")
    if intent in ("question", "other"):
        return _clarify(spec)
    if intent == "edit":
        return _route_edit(spec, current, confidence, context)
    reason = _unsupported_reason(spec)
    if reason:
        return _needs_ai(spec, reason)
    entry, why = choose(spec)
    if entry is None:
        subject = spec.get("subject") or {}
        if subject.get("family", "other") == "other" and not subject.get("kind") and not spec.get("features"):
            return _clarify(spec)
        return _needs_ai(spec, why)
    params, notes = params_from_spec(entry, spec)
    out = _generator_route(entry, params, notes, confidence)
    kind = ((spec.get("subject") or {}).get("kind") or "").strip().casefold()
    preset = pick_preset(entry, kind, (spec.get("subject") or {}).get("family") or "")
    if preset:
        out["preset"] = preset
        out["title"] = f"{entry['title']}: {entry['presets'][preset]['title']}"
    if kind and kind not in GENERIC_KINDS and kind != (spec.get("subject") or {}).get("family") and not (
            kind in entry["kinds"] or kind == preset or kind in ((entry.get("presets") or {}).get(preset) or {}).get("keywords", [])):
        out["closest"] = True
        out["notes"] = [f"closest design: {out['title']} (there is no exact {kind} design)", *out["notes"]]
    return out


def _route_edit(spec: dict, current: Optional[dict], confidence: float, context: Optional[dict] = None) -> dict:
    if not current and (context or {}).get("has_model"):
        return _route_role_edit(spec, context, confidence)
    if not current:
        return {"route": "clarify", "generic": True, "question": "There is nothing to change yet. What would you like to build?"}
    entry = _entry(current["generator"])
    if entry is None:
        return _needs_ai(spec, f"unknown generator {current['generator']!r}")
    edits = spec.get("edits") or []
    replace = next((e for e in edits if e.get("op") == "replace_model"), None)
    if replace:
        target, why = choose({"subject": {"family": "other", "kind": str(replace.get("value") or "")}})
        if target is None:
            return _needs_ai(spec, why)
        carried = {k: v for k, v in (current.get("params") or {}).items()
                   if entry["params"]["properties"].get(k, {}).get("x-colour") and k in target["params"]["properties"]}
        params, notes = clamp(target, carried)
        out = _generator_route(target, params, notes, confidence)
        out["preview"] = {"reasons": [f"replaces the {entry['title']} with the {target['title']}"], "summary": f"{entry['title']} to {target['title']}"}
        return out
    base, _ = clamp(entry, current.get("params"))
    params, changes, notes, history = edit_params(entry, base, edits)
    if not changes and not history:
        if notes:
            return _needs_ai(spec, "; ".join(notes[:2]))
        return {"route": "clarify", "generic": True, "question": "That already matches the current build. What would you like to change?"}
    out = {"route": "edit", "generator": entry["id"], "params": params, "changes": changes, "notes": notes,
           "confidence": round(confidence, 2), "cost_eur": 0.0, "seconds_estimate": SECONDS.get(entry["id"], DEFAULT_SECONDS)}
    if history:
        out["history"] = history
    elif preview := _preview(base, changes, bool(current.get("manual_edits"))):
        out["preview"] = preview
    return out


def _preview(base: dict, changes: list[dict], manual_edits: bool) -> Optional[dict]:
    """Why a change should be shown as a proposal before it is applied, or None for a small change (applied, with Undo).

    Big: a size parameter moves by more than BIG_CHANGE, or the draft carries manual edits that a regenerate would replace.
    """
    reasons = []
    for change in changes:
        old, new = base.get(change["param"]), change["to"]
        if change["param"] in (*SIZE_PARAMS, "courses") and isinstance(old, (int, float)) and isinstance(new, (int, float)) \
                and old and abs(new - old) / abs(old) > BIG_CHANGE:
            reasons.append(f"{change['label']} changes by {abs(new - old) / abs(old):.0%}")
    if manual_edits and changes:
        reasons.append("the draft has manual edits that this change replaces")
    if not reasons:
        return None
    summary = ", ".join(f"{c['label']} {c['from']} to {c['to']}" for c in changes[:4])
    return {"reasons": reasons, "summary": summary}


# ---------------------------------------------------------------- rules-only guess (the composer hint)

def _current_spec(current: Optional[dict]) -> Optional[dict]:
    entry = _entry(current["generator"]) if current else None
    return spec_from_params(entry, current.get("params") or {}) if entry else None


def _rules_spec(text: str, current: Optional[dict], context: Optional[dict], previous: Optional[str]) -> tuple[Optional[dict], bool]:
    """(spec, certain) from the exact rules both the hint and the decision use: role words on a build with no live
    generator, the last change for "again"/"a bit less", then presets/undo/"make the <role> <colour>"."""
    context = context or {}
    if not current and context.get("has_model"):
        spec = understand.role_rules(text, previous)
        if spec:
            return spec, True
    spec = relative_spec(text, context.get("last")) if current else None
    spec = spec or understand.rules_shortcut(text, _current_spec(current) or (_model_spec() if context.get("has_model") else None), previous)
    return spec, bool(spec)


def _guess_spec(text: str, current: Optional[dict], context: Optional[dict] = None, previous: Optional[str] = None) -> tuple[dict, bool]:
    """(spec, certain) from rules alone: exact patterns first (certain), then the keyword reading."""
    spec, certain = _rules_spec(text, current, context, previous)
    if spec:
        return spec, certain
    spec_now = _current_spec(current) or (_model_spec() if (context or {}).get("has_model") else None)
    return _with_kind(understand.heuristic_spec(text, spec_now), text), False


def _with_kind(spec: dict, text: str) -> dict:
    """The keyword reading knows only the family; the word that gave it is the kind ("helicopter" is no jet)."""
    if spec.get("intent") == "new_model" and not (spec.get("subject") or {}).get("kind"):
        pattern = dict(understand._FAMILIES).get(spec["subject"]["family"])
        found = pattern and re.search(rf"\b(?:{pattern})s?\b", " " + understand._key(text) + " ")
        if found:
            spec["subject"]["kind"] = found.group(0).strip()
    return spec


def rules_guess(text: str, current: Optional[dict] = None, context: Optional[dict] = None) -> dict:
    """Free and fast (no model call, no registry dump, cached chat history): the likely route and a hint line.

    Same rules and the same build context (live generator source, role edits on other builds) as the decision, so the
    hint and the answer agree whenever no model is needed. confidence is 1.0 for exact patterns.
    """
    started = time.monotonic()
    text = (text or "").strip()[: understand.MAX_REQUEST_CHARS]
    if len(text) < 3:
        return {"route": "clarify", "confidence": 0.0, "decided_by": "rules", "hint": ""}
    previous = (context or {}).get("previous")
    spec, certain = _guess_spec(text, current, context, previous)
    if _entries():
        decision = route(spec, current, context=context)
    else:                                                                   # registry not loaded yet: families alone
        family = (spec.get("subject") or {}).get("family")
        if spec.get("intent") == "new_model" and family in STATIC_FAMILIES:
            decision = {"route": "generator", "seconds_estimate": DEFAULT_SECONDS}
        elif spec.get("intent") == "edit":
            decision = {"route": "edit", "seconds_estimate": 1}
        else:
            decision = _needs_ai(spec, "no generator for this") if spec.get("intent") == "new_model" and (certain or family != "other") else _clarify(spec)
    kind = decision["route"]
    if certain:
        confidence = 1.0
    elif kind == "generator":
        confidence = min(0.8, 0.5 + 0.1 * sum(bool(spec.get(k)) for k in ("colours", "size")))
    else:
        confidence = 0.4 if (spec.get("subject") or {}).get("family", "other") != "other" else 0.3
    out = {"route": kind, "confidence": round(confidence, 2), "decided_by": "rules", "hint": ""}
    if kind in ("generator", "edit"):
        seconds = 1 if kind == "edit" else decision.get("seconds_estimate", DEFAULT_SECONDS)
        what = "; ".join(f"{c['label']}: {c.get('from') if c.get('from') is not None else '…'} → {c.get('to')}" for c in decision.get("changes") or [])
        out.update(generator=decision.get("generator"), cost_eur=0.0, seconds_estimate=seconds,
                   hint=f"Fast path: free, ~{seconds} s" + (f" · {what}" if what else ""))
    elif kind == "needs_ai":
        est = decision["estimate"]
        out.update(estimate=est, hint=f"AI build: ~€{est['eur_low']:g}–{est['eur_high']:g}, ~{est['minutes_low']}–{est['minutes_high']} min")
    out["took_ms"] = round((time.monotonic() - started) * 1000, 1)
    request_log.record_estimate(text, out, spec=spec, current=current)
    return out


# ---------------------------------------------------------------- the decision (one cheap call at most)

def confidence_low(result, spec: dict) -> bool:
    return result.source != "llm" or float(spec.get("confidence", 1.0)) < 0.6


_STEP_BREAK = re.compile(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b)\s+(?=(?:please\s+)?(?:make|paint|colou?r|turn|change|add|remove|delete|drop)\b"
                         r"|(?:bigger|smaller|longer|shorter|wider|narrower|taller|lower)\b)", re.I)


def split_steps(text: str) -> Optional[tuple[str, str, list[dict]]]:
    """("a red car", "make it longer", edits) when a new-model request is followed by edits the rules fully read, else None."""
    found = _STEP_BREAK.search(text)
    if not found:
        return None
    head, tail = text[:found.start()].strip(), text[found.end():].strip()
    edits = understand.rules_edits(tail) if head else None
    return (head, tail, edits) if edits else None


async def _describe_steps(head: str, tail: str, edits: list[dict], **kwargs) -> Optional[dict]:
    """Build the head request, then apply the edits on that result as a scratch plan; the steps report what each did.

    Only when the head is a generator build and every edit lands: otherwise None and the whole message goes the usual way.
    """
    decision = await describe(head, **kwargs)
    entry = _entry(decision["generator"]) if decision["route"] == "generator" else None
    if entry is None:
        return None
    params, changes, notes, history = edit_params(entry, decision["params"], edits)
    if history or not changes:
        return None
    steps = [{"step": 1, "route": "generator", "text": head, "generator": entry["id"], "params": decision["params"]},
             {"step": 2, "route": "edit", "text": tail, "changes": changes, "notes": notes}]
    return {**decision, "params": params, "notes": [*decision["notes"], *notes], "steps": steps}


def _unsure(spec: dict) -> bool:
    """A model reading that should be asked about, not applied: low confidence, or it flagged an ambiguity with a question."""
    return float(spec.get("confidence", 1.0)) < 0.5 or any(a.get("question") for a in spec.get("ambiguities") or [])


def context_text(current: Optional[dict], context: Optional[dict]) -> str:
    """The compact build summary and the last turns the understanding call reads follow-ups against."""
    context, parts = context or {}, []
    if current:
        shown = {k: v for k, v in (current.get("params") or {}).items() if not isinstance(v, (dict, list))}
        parts.append(f"build: generator {current['generator']} " + " ".join(f"{k}={v}" for k, v in list(shown.items())[:14]))
    elif context.get("has_model"):
        roles = ", ".join(f"{r} {n}" for r, n in (context.get("roles") or {}).items())
        parts.append(f"build: a model of {context.get('parts', '?')} parts" + (" edited by hand or by the AI, no live generator" if context.get("diverged") else ", not made by a generator")
                     + (f"; roles {roles}" if roles else ""))
    if context.get("selection"):
        parts.append(f"{context['selection']} parts selected on the canvas")
    turns = context.get("turns") or []
    if turns:
        parts.append("last turns: " + " | ".join(str(t)[:120] for t in turns[-3:]))
    return "; ".join(parts)


async def describe(text: str, *, current: Optional[dict] = None, build_id: Optional[str] = None,
                   previous_text: Optional[str] = None, llm_id: Optional[str] = None, context: Optional[dict] = None) -> dict:
    """Understand the request (rules first, else one structured call) and route it. See docs/plans/done/phase2/fast-path.md.

    `context` (optional): has_model, parts, roles, diverged, selection, last (the last change), turns (last turns as text).
    Without a live generator source (`current` None) but with a model, edits become role edits of the build's plan."""
    started = time.monotonic()
    await generator_bridge.ensure()
    context = context or {}
    if not current and not context.get("has_model") and (steps := split_steps(text)):
        combined = await _describe_steps(*steps, build_id=build_id, llm_id=llm_id)
        if combined:
            return combined
    role_build = bool(context.get("has_model")) and not current
    current_spec = _current_spec(current) or (_model_spec() if role_build else None)
    params = None
    spec, _ = _rules_spec(text, current, context, previous_text)
    if spec:
        result = understand.Result(spec, "rules")
    else:
        grammar = await grammar_decision(text, current)
        if grammar:
            grammar.update(understanding_cost_eur=0.0, understood_by="grammar", understanding_model=None)
            if build_id:
                grammar["build_id"] = build_id
            await asyncio.to_thread(request_log.record_describe, text, grammar, spec=None, started=started, current=current)
            return grammar
        if not understand.rules_shortcut(text, current_spec, previous_text):
            params = await understand.model_params(llm_id)
        result = await understand.understand(text, current_spec=current_spec, previous_text=previous_text, params=params,
                                             context=context_text(current, context))
    spec = _with_kind(result.spec or {}, text) if result.source == "rules_fallback" else result.spec or {}
    decision = route(spec, current, confidence=1.0 if result.source == "rules" else 0.7 if result.source == "llm" else 0.3,
                     context=context)
    cost = (result.usage or {}).get("cost_usd")
    spent = 0.0 if not result.usage else (None if cost is None else round(cost * settings.eur_per_usd(), 6))
    decision.update(understanding_cost_eur=spent, understood_by=result.source, understanding_model=result.model)
    if result.source == "rules_fallback" and decision["route"] == "generator":
        decision["notes"] = ["no understanding model answered; the request was read by keywords", *decision["notes"]]
    if result.errors and result.source != "llm":
        decision["understanding_errors"] = result.errors[:2]
    if result.source == "llm" and decision["route"] in ("edit", "generator") and _unsure(spec):
        # Apply nothing and ask, with the ambiguity's options as chips ("6x8 studs" / "6x8 bricks"), instead of resolving silently.
        extras = {k: v for k, v in decision.items() if k.startswith("understand")}
        decision = {**_clarify(spec), "spec": spec, **extras}
        if "choices" not in decision:
            decision["question"] = "I am not sure I read that right. Could you say it a little differently?"
    elif result.source != "rules" and ((current and decision["route"] in ("edit", "generator")) or (role_build and decision["route"] == "edit")) and (
            confidence_low(result, spec) or not chat_routing.whole_edit_command(text)):
        # An understood edit is applied only when the whole message is that edit: an edit word inside a longer instruction
        # ("go on... consider all bricks as dark grey") or a shaky reading is asked about, never applied.
        extras = {k: v for k, v in decision.items() if k.startswith("understand")}
        decision = {"route": "clarify", "generic": True, "spec": spec, **extras,
                    "question": "That looks like more than a quick change. What would you like to change?"}
    if build_id:
        decision["build_id"] = build_id
    await asyncio.to_thread(request_log.record_describe, text, decision, spec=spec, started=started,
                            usage_event_id=result.usage_event_id, current=current)
    return decision
