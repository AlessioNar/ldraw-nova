"""Deterministic generators: list, describe, and export plans from parameters (no LLM, no search)."""

import json
from pathlib import Path

from ..builder import build_plan
from ..common import atomic_write, dumps
from .base import Command


def add_generate(commands):
    c = commands.add_parser("generate", help="Deterministic model generators (house, vehicles, starfighter, recipes): list, params, plan")
    g = c.add_subparsers(dest="generate_command", required=True)
    v = g.add_parser("list", help="List generators with families, kinds and parameter names")
    v.add_argument("--family", help="Only generators for this spec family (car, house, spaceship...)")
    v.add_argument("--kind", help="Rank by a kind word such as cottage or pickup")
    g.add_parser("digest", help="Compact registry text for the understanding prompt, with its size")
    v = g.add_parser("show", help="Full registry entry: parameter schema, defaults, roles")
    v.add_argument("id")
    v = g.add_parser("colours", help="Resolve colour names (red, dark grey...) to LDraw codes")
    v.add_argument("names", nargs="*", help="Names to resolve; none lists the known names")
    v = g.add_parser("plan", help="Generate an editable plan from parameters and check that it builds")
    v.add_argument("id")
    v.add_argument("--params", default="{}", help='JSON object, e.g. \'{"width": 8, "windows": 2}\'')
    v.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="One parameter (JSON value or bare string); repeatable")
    v.add_argument("--output", help="Write the plan JSON here (the report then omits the plan)")
    v.add_argument("--force", action="store_true")
    v.add_argument("--no-check", action="store_true", help="Skip the build check (does not load the parts library)")


def needs_parts(args):
    return args.generate_command == "plan" and not args.no_check


def parse_params(args):
    params = json.loads(args.params)
    if not isinstance(params, dict):
        raise ValueError("--params must be a JSON object")
    for item in args.param:
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--param needs KEY=VALUE, got {item!r}")
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def run_generate(args, library, parts):
    from .. import generators
    from ..generators.colours import NAMES, colour_code, colour_names
    command = args.generate_command
    if command == "list":
        rows = generators.find(args.family, args.kind) if args.family or args.kind else list(generators.REGISTRY.values())
        return [dict(id=g.id, title=g.title, families=list(g.families), kinds=list(g.kinds), params=list(g.params), roles=list(g.roles))
                for g in rows], 0
    if command == "digest":
        text = generators.digest()
        return dict(digest=text, characters=len(text), approx_tokens=len(text) // 3), 0
    if command == "show":
        return generators.get(args.id).entry(), 0
    if command == "colours":
        if not args.names:
            return dict(primary=colour_names(), all={k: v for k, v in NAMES.items()}), 0
        return {name: colour_code(name) for name in args.names}, 0
    result = generators.generate(args.id, parse_params(args))
    report = dict(generator=result["generator"], params=result["params"], notes=result["notes"])
    if parts is not None:
        _, _, diagnostics = build_plan(result["plan"], parts)
        report["diagnostics"] = diagnostics
        report["checks_passed"] = not any(d["severity"] == "error" for d in diagnostics)
    if args.output:
        target = Path(args.output)
        if target.suffix.casefold() != ".json":
            raise ValueError("Plan output must end in .json")
        if target.exists() and not args.force:
            raise ValueError("Plan exists; use --force")
        if report.get("checks_passed", True):
            atomic_write(target, dumps(result["plan"]) + "\n")
        report["plan"] = str(target)
    else:
        report["plan"] = result["plan"]
    report["written"] = bool(args.output) and report.get("checks_passed", True)
    return report, 0 if report.get("checks_passed", True) else 1


COMMANDS = {
    "generate": Command(add_generate, run_generate, needs_parts=needs_parts),
}
