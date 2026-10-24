"""The fast-path decision engine: request text -> generator parameters, an edit, a clarifying question or "ask first".

    understand (rules shortcut, else ONE cheap structured call)  ->  route a spec.v1 to a generator  ->  validate and clamp

Nothing here builds. The result tells the caller (the build page) which deterministic generator to run with which
parameters, or that the paid AI build is needed, together with an estimate so the person can be asked before any
money is spent. Parameters are checked against the generators' JSON schemas (generator_bridge) and out-of-range values
are clamped with a note, the way the generators do it themselves. `rules_guess()` is the no-LLM, millisecond-fast
variant the composer calls while typing.
"""
from __future__ import annotations

import re
import statistics
import time
from typing import Any, Optional

import generator_bridge
import settings
import understand
import usage
from store import get_store

# Seconds a generator takes in the resident worker (build + checks + preview), from the proposal's measurements.
SECONDS = {"house": 15, "starfighter": 20, "arcade-bay": 10, "street-lantern": 10}
DEFAULT_SECONDS = 20
DEFAULT_ESTIMATE = {"eur_low": 1.0, "eur_high": 2.0, "minutes_low": 15, "minutes_high": 30}   # proposal: EUR 1.5-2, 24-35 min
MIN_HISTORY = 3
HISTORY_TTL_S = 60
# Families a generator exists for when the registry is not loaded yet (the estimate endpoint never waits for it).
STATIC_FAMILIES = {"car", "van", "truck", "motorcycle", "boat", "aircraft", "spaceship", "house", "building"}
GENERIC_KINDS = {"model", "vehicle", "building", "thing", "object", "toy"}
SIZE_PARAMS = ("length", "wingspan", "width", "depth")
WHOLE = ("whole", "model", "it", "", "size")

ROLE_PARAMS = {"body": ["body", "wall", "hull"], "hull": ["hull", "body", "wall"], "walls": ["wall", "body"],
               "wheels": ["wheels", "wheel"], "glass": ["glass", "canopy"], "canopy": ["canopy", "glass"],
               "roof": ["roof"], "trim": ["trim"], "accent": ["accent"], "door": ["door"], "wing": ["wing"],
               "chimney": ["chimney"], "engine": ["engine"]}
FEATURE_WORDS = {"door": "door", "window": "windows", "windows": "windows", "porch": "porch", "chimney": "chimney",
                 "dormer": "dormer", "engine": "engines", "engines": "engines", "wheel": "wheels", "wheels": "wheels"}
# scale word -> share of the way from the default to the maximum (negative: from the default to the minimum)
SCALE_SHARE = {"micro": -1.0, "mini": -0.5, "small": 0.0, "medium": 0.3, "large": 0.7}
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

def _kind_hit(entry: dict, kind: str) -> bool:
    kind = kind.casefold().strip()
    return bool(kind) and any(kind == k or kind in k or k in kind for k in entry["kinds"])


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
        rows.append((-(by_kind * 4 + by_family * 2 + (entry["families"][0] == family) + exact), len(entry["families"]), position, entry))
    if not rows:
        return None, f"no generator builds a {kind or family}"
    best = min(rows, key=lambda r: r[:3])[3]      # best score, then the more specific generator, then registry order
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


# ---------------------------------------------------------------- spec -> params

def _size_params(entry: dict, spec: dict, notes: list[str]) -> dict:
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
                default = schema[name].get("default", low)
                out[name] = round(default + (default - low) * share) if share < 0 else round(default + (high - default) * share)
    return out


def _colour_param(entry: dict, role: str) -> Optional[str]:
    schema = entry["params"]["properties"]
    for alias in ROLE_PARAMS.get(role, [role]):
        for name in (alias, f"{alias}_colour"):
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
        else:
            out[param] = True
    if "roof_style" in schema and "flat" in " ".join(f.get("name", "") for f in features).casefold():
        out["roof_style"] = "flat"
    return out


def params_from_spec(entry: dict, spec: dict) -> tuple[dict, list[str]]:
    """Generator params for a new-model spec, and the notes (what was assumed, left out or clamped)."""
    notes: list[str] = []
    wanted = _size_params(entry, spec, notes)
    for role, name in (spec.get("colours") or {}).items():
        param = _colour_param(entry, role)
        if param is None:
            notes.append(f"a {role} colour is not a setting of the {entry['id']} design; left out")
        else:
            wanted[param] = name
    wanted.update(_feature_params(entry, spec, notes))
    params, more = clamp(entry, wanted)
    return params, notes + more


def spec_from_params(entry: dict, params: dict) -> dict:
    """A small spec.v1 describing the current build, so the model reads a follow-up against it."""
    colours = {}
    for role in ("body", "roof", "trim", "wheels", "glass", "accent", "door", "wing", "chimney"):
        name = _colour_param(entry, role)
        if name and name in params:
            colours.setdefault(role, str(params[name]))
    studs = {k: params[k] for k in ("width", "depth", "length") if k in params}
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
    changes = [{"param": k, "label": k.replace("_", " "), "from": current.get(k), "to": params[k]}
               for k in params if params[k] != current.get(k)]
    return params, changes, notes + more, history


def _scale(schema: dict, wanted: dict, edit: dict, target: str) -> bool:
    delta = str(edit.get("delta", "")).casefold()
    sign = -1 if delta in ("smaller", "shorter", "lower", "narrower") else 1
    amount, unit = edit.get("amount"), edit.get("unit")
    if target == "roof" and "roof_tiers" in schema:
        names = ["roof_tiers"]
    elif (unit == "courses" or delta in ("taller", "lower")) and "courses" in schema:
        names = ["courses"]
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
    else:
        return False
    return True


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
    out = {"route": "clarify",
           "question": asked or 'What would you like to build? For example "a small red car" or "a cottage 10 studs wide".'}
    if ambiguity.get("options"):
        out["choices"] = [str(o) for o in ambiguity["options"][:4]]
    return out


def _needs_ai(spec: dict, reason: str) -> dict:
    return {"route": "needs_ai", "spec": spec, "reason": reason, "estimate": ai_estimate()}


def _generator_route(entry: dict, params: dict, notes: list[str], confidence: float) -> dict:
    return {"route": "generator", "generator": entry["id"], "title": entry["title"], "params": params, "notes": notes,
            "confidence": round(confidence, 2), "cost_eur": 0.0, "seconds_estimate": SECONDS.get(entry["id"], DEFAULT_SECONDS)}


def route(spec: dict, current: Optional[dict] = None, confidence: float = 0.7) -> dict:
    """The decision for one understood spec (no LLM; only the cached registry and the cached chat history)."""
    confidence = spec.get("confidence", confidence)
    intent = spec.get("intent")
    if intent in ("question", "other"):
        return _clarify(spec)
    if intent == "edit":
        return _route_edit(spec, current, confidence)
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
    return _generator_route(entry, params, notes, confidence)


def _route_edit(spec: dict, current: Optional[dict], confidence: float) -> dict:
    if not current:
        return {"route": "clarify", "question": "There is nothing to change yet. What would you like to build?"}
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
        return _generator_route(target, params, notes, confidence)
    base, _ = clamp(entry, current.get("params"))
    params, changes, notes, history = edit_params(entry, base, edits)
    if not changes and not history:
        if notes:
            return _needs_ai(spec, "; ".join(notes[:2]))
        return {"route": "clarify", "question": "That already matches the current build. What would you like to change?"}
    out = {"route": "edit", "generator": entry["id"], "params": params, "changes": changes, "notes": notes,
           "confidence": round(confidence, 2), "cost_eur": 0.0, "seconds_estimate": SECONDS.get(entry["id"], DEFAULT_SECONDS)}
    if history:
        out["history"] = history
    return out


# ---------------------------------------------------------------- rules-only guess (the composer hint)

def _current_spec(current: Optional[dict]) -> Optional[dict]:
    entry = _entry(current["generator"]) if current else None
    return spec_from_params(entry, current.get("params") or {}) if entry else None


def _guess_spec(text: str, current_spec: Optional[dict]) -> tuple[dict, bool]:
    """(spec, certain) from rules alone: exact patterns first (certain), then the keyword reading."""
    spec = understand.rules_shortcut(text, current_spec)
    if spec:
        return spec, True
    return _with_kind(understand.heuristic_spec(text, current_spec), text), False


def _with_kind(spec: dict, text: str) -> dict:
    """The keyword reading knows only the family; the word that gave it is the kind ("helicopter" is no jet)."""
    if spec.get("intent") == "new_model" and not (spec.get("subject") or {}).get("kind"):
        pattern = dict(understand._FAMILIES).get(spec["subject"]["family"])
        found = pattern and re.search(rf"\b(?:{pattern})s?\b", " " + understand._key(text) + " ")
        if found:
            spec["subject"]["kind"] = found.group(0).strip()
    return spec


def rules_guess(text: str, current: Optional[dict] = None) -> dict:
    """Free and fast (no model call, no registry dump, cached chat history): the likely route and a hint line.

    confidence is 1.0 for exact patterns (presets, 'make the roof blue') and lower for the keyword reading; the real
    /api/describe may decide differently once the cheap model has read the text.
    """
    started = time.monotonic()
    text = (text or "").strip()[: understand.MAX_REQUEST_CHARS]
    if len(text) < 3:
        return {"route": "clarify", "confidence": 0.0, "decided_by": "rules", "hint": ""}
    spec, certain = _guess_spec(text, _current_spec(current))
    if _entries():
        decision = route(spec, current)
    else:                                                                   # registry not loaded yet: families alone
        family = (spec.get("subject") or {}).get("family")
        if spec.get("intent") == "new_model" and family in STATIC_FAMILIES:
            decision = {"route": "generator", "seconds_estimate": DEFAULT_SECONDS}
        else:
            decision = _needs_ai(spec, "no generator for this") if spec.get("intent") == "new_model" else _clarify(spec)
    if decision["route"] == "clarify" and not certain and spec.get("intent") == "new_model" and len(text.split()) >= 3:
        decision = _needs_ai(spec, "unknown subject")                       # a real request the keywords cannot place: likely the AI build
    kind = decision["route"]
    if certain:
        confidence = 1.0
    elif kind == "generator":
        confidence = min(0.8, 0.5 + 0.1 * sum(bool(spec.get(k)) for k in ("colours", "size")))
    else:
        confidence = 0.4 if (spec.get("subject") or {}).get("family", "other") != "other" else 0.3
    out = {"route": kind, "confidence": round(confidence, 2), "decided_by": "rules", "hint": ""}
    if kind in ("generator", "edit"):
        seconds = decision.get("seconds_estimate", DEFAULT_SECONDS)
        out.update(generator=decision.get("generator"), cost_eur=0.0, seconds_estimate=seconds, hint=f"Fast path: free, ~{seconds} s")
    elif kind == "needs_ai":
        est = decision["estimate"]
        out.update(estimate=est, hint=f"AI build: ~€{est['eur_low']:g}–{est['eur_high']:g}, ~{est['minutes_low']}–{est['minutes_high']} min")
    out["took_ms"] = round((time.monotonic() - started) * 1000, 1)
    return out


# ---------------------------------------------------------------- the decision (one cheap call at most)

async def describe(text: str, *, current: Optional[dict] = None, build_id: Optional[str] = None,
                   previous_text: Optional[str] = None, llm_id: Optional[str] = None) -> dict:
    """Understand the request (rules first, else one structured call) and route it. See docs/plans/phase2/fast-path.md."""
    await generator_bridge.ensure()
    current_spec = _current_spec(current)
    params = None
    if not understand.rules_shortcut(text, current_spec, previous_text):
        params = await understand.model_params(llm_id)
    result = await understand.understand(text, current_spec=current_spec, previous_text=previous_text, params=params)
    spec = _with_kind(result.spec or {}, text) if result.source == "rules_fallback" else result.spec or {}
    decision = route(spec, current,
                     confidence=1.0 if result.source == "rules" else 0.7 if result.source == "llm" else 0.3)
    cost = (result.usage or {}).get("cost_usd")
    spent = 0.0 if not result.usage else (None if cost is None else round(cost * settings.eur_per_usd(), 6))
    decision.update(understanding_cost_eur=spent, understood_by=result.source, understanding_model=result.model)
    if result.source == "rules_fallback" and decision["route"] == "generator":
        decision["notes"] = ["no understanding model answered; the request was read by keywords", *decision["notes"]]
    if result.errors and result.source != "llm":
        decision["understanding_errors"] = result.errors[:2]
    if build_id:
        decision["build_id"] = build_id
    return decision
