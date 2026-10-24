"""The understanding step: one user message (or follow-up) -> spec.v1 / edit intents.

    rules shortcut (free)  ->  one single-shot structured call (cheap model)  ->  one retry  ->  rules fallback

The LLM only reads natural language here. It never sees parts, plans or tools, and it builds nothing; the output is
data for the deterministic router (see docs/plans/deterministic-first/PROPOSAL.md, section 3a). Not wired into the
chat turn yet: `understand()` is the entry point and routers/understand.py a small internal endpoint for testing.

Prompt layout, built for provider prompt caching: a fixed prefix (instructions, registry digest, colour list; the
schema travels as the tool definition that precedes it) that is byte-identical for every call while the digest is
unchanged, then a variable tail (options, current spec, the request). `prefix()` and `tool()` are pure functions of
the digest, and tests pin that.

Registry digest interface (consumed here, produced by ldraw_tools/generators/): a callable
`ldraw_tools.generators.registry_digest() -> str`, one line per registry entry, in this column order

    <id> | <families, comma separated> | <kinds/synonyms, comma separated> | <param:values ...> | <roles, comma separated>

Anything else about the text is free. `load_digest()` falls back to FALLBACK_DIGEST (the seven vehicle designs and
the planned families) when the registry is absent or raises, so this module works before and after it lands.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import jsonschema
import litellm

import model_catalog
import providers
import usage

log = logging.getLogger("ldraw-nova.understand")

DEFAULT_MODEL = "anthropic/claude-haiku-5-5"
MAX_REQUEST_CHARS = 2000          # a longer message is cut: the step reads a request, not a document
MAX_OUTPUT_TOKENS = 500           # per-message cap from the proposal (3k in, 500 out)
TOOL_NAME = "emit_spec"
_HERE = Path(__file__).parent

SPEC_SCHEMA = json.loads((_HERE / "spec.v1.schema.json").read_text(encoding="utf-8"))
EDIT_SCHEMA = json.loads((_HERE / "edit-intent.v1.schema.json").read_text(encoding="utf-8"))
_SPEC_VALIDATOR = jsonschema.Draft202012Validator(SPEC_SCHEMA)
_EDIT_VALIDATOR = jsonschema.Draft202012Validator(EDIT_SCHEMA)

# Rebrickable names (ldraw_tools/data/rebrickable-colours.json), the ones a casual request names.
COLOURS = ["White", "Black", "Red", "Blue", "Yellow", "Green", "Orange", "Tan", "Brown", "Dark Bluish Gray",
           "Light Bluish Gray", "Dark Gray", "Light Gray", "Dark Red", "Dark Blue", "Dark Green", "Dark Tan",
           "Dark Orange", "Medium Blue", "Bright Light Blue", "Sand Green", "Lime", "Olive Green", "Pink",
           "Dark Pink", "Purple", "Magenta", "Reddish Brown", "Dark Brown", "Medium Azure", "Dark Azure",
           "Light Nougat", "Medium Nougat", "Bright Light Orange", "Pearl Gold", "Flat Silver", "Trans-Clear",
           "Trans-Light Blue", "Trans-Red", "Trans-Yellow", "Trans-Green", "Trans-Black"]
COLOUR_ALIASES = {"grey": "Light Bluish Gray", "gray": "Light Bluish Gray", "light grey": "Light Gray",
                  "dark grey": "Dark Gray", "light bluish grey": "Light Bluish Gray", "dark bluish grey": "Dark Bluish Gray",
                  "gold": "Pearl Gold", "silver": "Flat Silver", "clear": "Trans-Clear", "transparent": "Trans-Clear",
                  "navy": "Dark Blue", "beige": "Tan", "violet": "Purple", "crimson": "Dark Red", "lime green": "Lime"}
_COLOUR_BY_LOWER = {**{c.lower(): c for c in COLOURS}, **{k: v for k, v in COLOUR_ALIASES.items()}}
_COLOUR_RE = "|".join(re.escape(k) for k in sorted(_COLOUR_BY_LOWER, key=len, reverse=True))

FALLBACK_DIGEST = """\
grand-tourer | car | coupe, sports car, grand tourer, racer | palette:heritage-racing,coastal-delivery,desert-utility | body,roof,trim,wheels,glass,accent
delivery-van | van | van, delivery van, minibus | palette:heritage-racing,coastal-delivery,desert-utility | body,roof,trim,wheels,glass,accent
pickup | truck | pickup, pick-up truck, ute | palette:heritage-racing,coastal-delivery,desert-utility | body,cab,bed,trim,wheels,glass
tipper-truck | truck | truck, tipper truck, dump truck, lorry | palette:heritage-racing,coastal-delivery,desert-utility | body,cab,bed,trim,wheels,glass
touring-motorcycle | motorcycle | motorbike, motorcycle, bike | palette:heritage-racing,coastal-delivery,desert-utility | body,trim,wheels,accent
harbour-launch | boat | boat, launch, small boat | palette:heritage-racing,coastal-delivery,desert-utility | hull,trim,glass,accent
courier-jet | aircraft | jet, small plane, aeroplane | palette:heritage-racing,coastal-delivery,desert-utility | body,wing,glass,accent
house | house | house, cottage, cabin, shed, shop | width,depth,courses,door,windows,roof-tiers,porch,chimney,dormer | body,roof,trim,door,glass
tower | tower | tower, castle, lighthouse | width,courses,stair,battlements | body,roof,trim
spaceship | spaceship | starfighter, freighter, starship, shuttle | wing-span,engines,canopy | hull,accent,glass
reference-models | any | named real sets, franchises, technic mechanisms, trains, cranes | text search | any\
"""


@dataclass(frozen=True)
class Digest:
    text: str
    version: str


def make_digest(text: str) -> Digest:
    text = "\n".join(line.rstrip() for line in text.strip().splitlines())
    return Digest(text, hashlib.sha256(text.encode()).hexdigest()[:12])


def load_digest() -> Digest:
    """The registry digest (interface in the module docstring), or the built-in one when the registry is missing."""
    import generator_bridge
    registry = generator_bridge.cached()
    text = (registry or {}).get("digest")
    if isinstance(text, str) and text.strip():
        return make_digest(text)
    return make_digest(FALLBACK_DIGEST)


INSTRUCTIONS = """\
You turn a LEGO building request into JSON by calling the tool emit_spec exactly once. You build nothing and never answer in prose.

intent: new_model for a fresh build request; edit when CURRENT_SPEC is given and the user changes that model; question when the user only asks something; other for anything else (greeting, unclear).
subject.family: the closest family. kind: a short noun, preferring a kind from the GENERATORS list. For an edit copy the subject of CURRENT_SPEC.
size.scale: only when stated or implied (tiny=micro, mini, small, medium, large, big/huge=large). size.studs: only explicit numbers. For "A x B" with no unit set studs width A, depth B, unit_ambiguous true and one ambiguities entry (field size, a short question, options "AxB studs" and "AxB bricks"). With the unit stated, unit_ambiguous false.
colours: role -> colour name, only names from COLOURS, only colours the user stated. Roles: body, roof, trim, wheels, glass, accent (others allowed, e.g. hull). "white and blue" = body White, accent Blue. "black wheels" = wheels Black.
features: things to include (name, count, where); required true only when the user insists.
constraints: only what the user states (parts_mode off|prefer|only, max_parts, budget_eur, must_use, avoid). OPTIONS are chat defaults: do not copy them.
edits (follow-ups only, one per change, empty for a new model): set_colour target role value colour; scale_param target (wheels, whole...) delta bigger|smaller|longer|shorter|wider|taller|lower with optional amount+unit studs|courses|percent; set_param target value; add_feature / remove_feature target feature, optional count, where; swap_part target value; replace_model value family or design; undo; revert. Target whole means the entire model.
reference_hint: only a named real set or franchise.
ambiguities: only when it would change what gets built; at most 2, each with a short question and options.
confidence: 0 to 1. Omit every field you do not need.

Examples:
"make the roof blue" with CURRENT_SPEC -> {"intent":"edit","subject":{"family":"house"},"edits":[{"op":"set_colour","target":"roof","value":"Blue"}]}
"bigger wheels, and add a chimney" -> {"intent":"edit","subject":{"family":"house"},"edits":[{"op":"scale_param","target":"wheels","delta":"bigger"},{"op":"add_feature","target":"chimney"}]}
"a small white and blue starship" -> {"intent":"new_model","subject":{"family":"spaceship","kind":"starship"},"size":{"scale":"small"},"colours":{"hull":"White","accent":"Blue"}}\
"""

TOOL_DESCRIPTION = "Report the understood request as spec.v1. Call exactly once."


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _tool_schema() -> dict:
    return {k: v for k, v in SPEC_SCHEMA.items() if k not in ("$schema", "$id", "title", "description")}


def prefix(digest: Optional[Digest] = None) -> str:
    """The cacheable system prompt. A pure function of the digest: byte-identical across calls."""
    digest = digest or load_digest()
    return (f"{INSTRUCTIONS}\n\nGENERATORS (id | families | kinds | params | roles):\n{digest.text}\n\n"
            f"COLOURS: {', '.join(COLOURS)}")


def tool() -> dict:
    """The tool definition: the spec.v1 schema, serialised with sorted keys so it is byte-stable too."""
    return {"type": "function", "function": {"name": TOOL_NAME, "description": TOOL_DESCRIPTION,
                                             "parameters": json.loads(_compact(_tool_schema()))}}


def tail(text: str, current_spec: Optional[dict] = None, options: Optional[dict] = None, retry_note: str = "") -> str:
    """The variable part of the prompt: options, current spec (follow-ups only), the request."""
    lines = []
    opts = {k: v for k, v in (options or {}).items() if v not in (None, "")}
    if opts:
        lines.append("OPTIONS: " + "; ".join(f"{k}={v}" for k, v in sorted(opts.items())))
    if current_spec:
        lines.append("CURRENT_SPEC: " + _compact(current_spec))
    lines.append("REQUEST: " + text[:MAX_REQUEST_CHARS])
    if retry_note:
        lines.append("YOUR PREVIOUS OUTPUT WAS INVALID (" + retry_note[:300] + "). Call emit_spec again with valid arguments.")
    return "\n".join(lines)


def messages_for(model: str, digest: Digest, text: str, current_spec=None, options=None, retry_note: str = "") -> list[dict]:
    system = prefix(digest)
    if providers.supports_prompt_caching(model):
        system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return [{"role": "system", "content": system},
            {"role": "user", "content": tail(text, current_spec, options, retry_note)}]


# ---------------------------------------------------------------- validation and normalisation

def validate(spec: Any) -> list[str]:
    """Problems of a spec (empty when valid): spec.v1, plus edit-intent.v1 when intent is edit."""
    if not isinstance(spec, dict):
        return ["output is not a JSON object"]
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in _SPEC_VALIDATOR.iter_errors(spec)]
    if not errors and spec.get("intent") == "edit":
        errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in _EDIT_VALIDATOR.iter_errors(spec)]
    return errors[:5]


def canonical_colour(name: Any) -> Optional[str]:
    return _COLOUR_BY_LOWER.get(re.sub(r"[\s_]+", " ", str(name)).strip().lower())


def normalise(spec: dict) -> tuple[dict, list[str]]:
    """Canonical colour names; unknown colours are kept and reported (the router decides what to do with them)."""
    warnings = []
    for role, name in (spec.get("colours") or {}).items():
        known = canonical_colour(name)
        if known:
            spec["colours"][role] = known
        else:
            warnings.append(f"unknown colour {name!r} for {role}")
    for edit in spec.get("edits") or []:
        if edit.get("op") == "set_colour" and isinstance(edit.get("value"), str):
            known = canonical_colour(edit["value"])
            if known:
                edit["value"] = known
            else:
                warnings.append(f"unknown colour {edit['value']!r} for {edit.get('target')}")
    return spec, warnings


# ---------------------------------------------------------------- rules (free shortcut and last-resort fallback)

ROLES = {"body": "body", "roof": "roof", "wheels": "wheels", "wheel": "wheels", "trim": "trim", "glass": "glass",
         "windows": "glass", "window": "glass", "door": "door", "cab": "cab", "bed": "bed", "chimney": "chimney",
         "hull": "hull", "wings": "wing", "wing": "wing", "accent": "accent", "walls": "body", "wall": "body"}
_ROLE_RE = "|".join(sorted(ROLES, key=len, reverse=True))
_RECOLOUR = re.compile(rf"^(?:(?:please|can you|could you|would you|just) )*(?:make|paint|colou?r|turn|change)\s+(?:the\s+)?({_ROLE_RE})\s+(?:to\s+|into\s+)?({_COLOUR_RE})(?:\s+please)?$")
_UNDO = re.compile(r"^(?:please )?(?:undo|undo that|undo the last change|undo last change|go back)$")
_REVERT = re.compile(r"^(?:please )?revert(?: to (?:the )?(?:original|first|previous|initial)(?: version| model| design)?)?$")
_AGAIN = {"again", "do it again", "once more", "repeat", "repeat that"}

# Exact home-page suggestions (app/web/frontend/src/pages/Home.tsx) answered with no call at all.
_PRESET_SPECS = {
    "Build a small red car with four black wheels": {
        "intent": "new_model", "subject": {"family": "car", "kind": "car"}, "size": {"scale": "small"},
        "colours": {"body": "Red", "wheels": "Black"}, "features": [{"name": "wheels", "count": 4}]},
    "A 6 x 8 cottage with a door, two windows and a sloped roof": {
        "intent": "new_model", "subject": {"family": "house", "kind": "cottage"},
        "size": {"studs": {"width": 6, "depth": 8}, "unit_ambiguous": True},
        "features": [{"name": "door", "count": 1}, {"name": "window", "count": 2}, {"name": "sloped roof"}],
        "ambiguities": [{"field": "size", "question": "Is 6 x 8 in studs or in bricks?", "options": ["6x8 studs", "6x8 bricks"]}]},
    "Build a spiral staircase around a tall stone tower": {
        "intent": "new_model", "subject": {"family": "tower", "kind": "stone tower"},
        "colours": {"body": "Light Bluish Gray"}, "features": [{"name": "spiral staircase", "required": True}]},
    "Design a harbour crane with a long boom and a cargo dock": {
        "intent": "new_model", "subject": {"family": "other", "kind": "harbour crane"},
        "features": [{"name": "long boom"}, {"name": "cargo dock"}]},
}


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?, ")


PRESETS = {_key(k): v for k, v in _PRESET_SPECS.items()}


def _edit_spec(current: Optional[dict], *edits: dict) -> dict:
    subject = (current or {}).get("subject") or {"family": "other"}
    return {"intent": "edit", "subject": json.loads(json.dumps(subject)), "edits": list(edits), "confidence": 1.0}


def rules_shortcut(text: str, current_spec: Optional[dict] = None, previous_text: Optional[str] = None) -> Optional[dict]:
    """Trivial cases answered for free: UI presets, undo/revert, 'make the <role> <colour>', 'again'. None otherwise."""
    key = _key(text)
    if key in PRESETS and not current_spec:
        return json.loads(json.dumps(PRESETS[key]))
    if _UNDO.match(key):
        return _edit_spec(current_spec, {"op": "undo", "target": "whole"})
    if _REVERT.match(key):
        return _edit_spec(current_spec, {"op": "revert", "target": "whole"})
    found = _RECOLOUR.match(key)
    if found and current_spec:
        return _edit_spec(current_spec, {"op": "set_colour", "target": ROLES[found.group(1)], "value": _COLOUR_BY_LOWER[found.group(2)]})
    if key in _AGAIN and previous_text and _key(previous_text) not in _AGAIN:
        return rules_shortcut(previous_text, current_spec)       # repeat only what rules themselves understand
    if current_spec and previous_text and key == _key(previous_text):
        return json.loads(json.dumps(current_spec))               # identical message: same spec
    return None


_FAMILIES = [("spaceship", r"space ?ship|starship|spacecraft|starfighter|rocket|shuttle|freighter"),
             ("motorcycle", r"motor ?bike|motorcycle|bike|scooter"),
             ("aircraft", r"plane|jet|aeroplane|airplane|helicopter|aircraft"),
             ("boat", r"boat|yacht|ferry|launch|ship|tugboat"),
             ("truck", r"truck|lorry|tipper|pick-?up|tractor"),
             ("van", r"van|bus|minibus"),
             ("car", r"car|coupe|sedan|racer|taxi|limo|jeep"),
             ("technic", r"technic"),
             ("mechanism", r"gearbox|gears?|mechanism|differential|suspension|worm drive"),
             ("tower", r"tower|castle|lighthouse|windmill"),
             ("house", r"house|cottage|cabin|villa|shed|home|hut"),
             ("building", r"shop|store|museum|station|church|school|building|gatehouse|bakery|hotel"),
             ("garden", r"garden|park|fountain|playground")]
_SCALES = [("micro", r"tiny|micro"), ("mini", r"mini"), ("small", r"small|little"), ("large", r"large|big|huge|giant"), ("medium", r"medium")]
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def heuristic_spec(text: str, current_spec: Optional[dict] = None) -> dict:
    """Last-resort reading when the model is unavailable or keeps returning garbage: keywords, colours, sizes."""
    low = " " + _key(text) + " "
    shortcut = rules_shortcut(text, current_spec)
    if shortcut:
        return shortcut
    if current_spec:
        edits = []
        for verb, op in (("add", "add_feature"), ("remove", "remove_feature")):
            for noun in re.findall(rf"\b{verb} (?:a |an |the |some )?([a-z ]{{2,25}}?)(?: and |,|$)", low.strip()):
                edits.append({"op": op, "target": noun.strip()})
        if edits:
            return {**_edit_spec(current_spec, *edits), "confidence": 0.3}
        return {"intent": "other", "subject": json.loads(json.dumps(current_spec.get("subject") or {"family": "other"})), "confidence": 0.1}
    family = next((f for f, pattern in _FAMILIES if re.search(rf"\b(?:{pattern})s?\b", low)), "other")
    spec: dict = {"intent": "new_model", "subject": {"family": family}, "confidence": 0.3}
    scale = next((s for s, pattern in _SCALES if re.search(rf"\b(?:{pattern})\b", low)), None)
    if scale:
        spec["size"] = {"scale": scale}
    dims = re.search(r"\b(\d+)\s*(?:x|×|by)\s*(\d+)\b(\s*(?:studs?|bricks?))?", low)
    if dims:
        size = spec.setdefault("size", {})
        size["studs"] = {"width": int(dims.group(1)), "depth": int(dims.group(2))}
        if not dims.group(3):
            size["unit_ambiguous"] = True
            spec["ambiguities"] = [{"field": "size", "question": f"Is {dims.group(1)} x {dims.group(2)} in studs or in bricks?",
                                    "options": [f"{dims.group(1)}x{dims.group(2)} studs", f"{dims.group(1)}x{dims.group(2)} bricks"]}]
    colours: dict[str, str] = {}
    spans = []
    for found in re.finditer(rf"\b({_COLOUR_RE})\b(?:\s+(?:\w+\s+)?)?({_ROLE_RE})\b", low):     # "black wheels", "red tiled roof"
        colours.setdefault(ROLES[found.group(2)], _COLOUR_BY_LOWER[found.group(1)])
        spans.append(found.span(1))
    loose = [(m.start(), _COLOUR_BY_LOWER[m.group(1)]) for m in re.finditer(rf"\b({_COLOUR_RE})\b", low) if m.span(1) not in spans]
    for (_, name), role in zip(loose, ["body", "accent"]):
        colours.setdefault(role, name)
    if colours:
        spec["colours"] = colours
    return spec


# ---------------------------------------------------------------- the model call

@dataclass
class Result:
    spec: Optional[dict]
    source: str                         # preset/rules | llm | rules_fallback | none
    attempts: int = 0                   # model calls made (0 when rules answered)
    usage: Optional[dict] = None        # summed over attempts: prompt/completion/cache_read/cache_write, cost_usd
    latency_s: float = 0.0
    model: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    digest_version: Optional[str] = None

    def as_dict(self) -> dict:
        return {"spec": self.spec, "source": self.source, "attempts": self.attempts, "usage": self.usage,
                "latency_s": round(self.latency_s, 3), "model": self.model, "errors": self.errors,
                "warnings": self.warnings, "digest_version": self.digest_version}


def _tool_choice(model: str):
    return "required" if providers.of(model).prefix == "mistral/" else {"type": "function", "function": {"name": TOOL_NAME}}


def _arguments(response) -> Any:
    """The tool call's arguments as an object; a model that answered in text gets its JSON read out of the text."""
    message = response.choices[0].message
    calls = getattr(message, "tool_calls", None) or []
    raw = calls[0].function.arguments if calls else (getattr(message, "content", None) or "")
    if isinstance(raw, dict):
        return raw
    raw = str(raw).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    return json.loads(raw)


def _add_usage(total: Optional[dict], record: Optional[dict], cost: Optional[float]) -> Optional[dict]:
    if record is None:
        return total
    total = total or {"prompt": 0, "completion": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0}
    for key in ("prompt", "completion", "cache_read", "cache_write"):
        total[key] += record[key]
    if cost is None:
        total["cost_usd"] = None
    elif total["cost_usd"] is not None:
        total["cost_usd"] = round(total["cost_usd"] + cost, 8)
    return total


async def understand(text: str, *, current_spec: Optional[dict] = None, previous_text: Optional[str] = None,
                     options: Optional[dict] = None, params: Optional[dict] = None, digest: Optional[Digest] = None,
                     use_rules: bool = True) -> Result:
    """One user message -> a spec.v1 dict, with the cost of getting it.

    `params` are the resolved litellm parameters of the chat's worker model (inference.params_for); None means no
    model is configured and only rules answer. A malformed answer is retried once (the error is told to the model in
    the variable tail, the cached prefix is untouched); a second failure falls back to the keyword rules.
    """
    started = time.monotonic()
    text = (text or "").strip()
    if not text:
        return Result(None, "none", errors=["empty request"])
    if use_rules:
        spec = rules_shortcut(text, current_spec, previous_text)
        if spec:
            return Result(spec, "rules", latency_s=time.monotonic() - started)
    digest = digest or load_digest()
    result = Result(None, "rules_fallback", digest_version=digest.version)
    if not params:
        result.errors.append("no model configured")
    else:
        model = params["model"]
        result.model = model
        pricing = model_catalog.profile(model).get("pricing")
        note = ""
        for _ in range(2):
            result.attempts += 1
            call = {k: v for k, v in params.items() if k not in ("messages", "stream", "tools", "tool_choice", "num_retries")}
            call.update(max_tokens=MAX_OUTPUT_TOKENS, num_retries=0)
            call.setdefault("timeout", 30)
            try:
                response = await litellm.acompletion(
                    **call, messages=messages_for(model, digest, text, current_spec, options, note),
                    tools=[tool()], tool_choice=_tool_choice(model))
                record = usage.usage_record(response)
                result.usage = _add_usage(result.usage, record, usage.step_cost_usd(record, pricing, model) if record else None)
                spec = _arguments(response)
                problems = validate(spec)
            except Exception as exc:  # noqa: BLE001 - provider error or unparseable output: one retry, then the rules
                spec, problems = None, [f"{exc.__class__.__name__}: {str(exc)[:200]}"]
            if not problems:
                result.spec, result.warnings = normalise(spec)
                result.source = "llm"
                break
            note = "; ".join(problems)
            result.errors.append(note)
    if result.spec is None:
        result.spec = heuristic_spec(text, current_spec)
    result.latency_s = time.monotonic() - started
    log.info("understand source=%s model=%s attempts=%d usage=%s latency=%.2fs", result.source, result.model,
             result.attempts, result.usage, result.latency_s)
    return result


async def model_params(llm_id: Optional[str] = None) -> Optional[dict]:
    """Resolved litellm parameters of the model that reads requests, or None when none is configured.

    Order: the given entry id, LDRAW_NOVA_UNDERSTAND_LLM_ID, the Settings "Understanding model", a configured entry
    that runs DEFAULT_MODEL (Haiku 5.5), then the main model. Any model that handles a forced tool call works
    (Mistral Small 4 included).
    """
    import os

    import inference
    import llm_config
    wanted = llm_id or os.environ.get("LDRAW_NOVA_UNDERSTAND_LLM_ID", "").strip() or llm_config.understanding_default()
    entry = llm_config.get(wanted) if wanted else None
    if entry is None:
        entries, main_id = llm_config.builder_entries()
        usable = [e for e in entries if not llm_config.unsupported(e)]
        entry = (next((e for e in usable if e["litellm_params"].get("model") == DEFAULT_MODEL), None)
                 or next((e for e in usable if e["id"] == main_id), None))
    if entry is None or llm_config.unsupported(entry):
        return None
    return await inference.params_for(entry, {})
