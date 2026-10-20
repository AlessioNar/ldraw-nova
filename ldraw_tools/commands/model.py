"""Build plans into MPDs and check, inspect, snap or list connectors of existing models."""

from pathlib import Path

from ..builder import build_plan, load_plan
from ..common import atomic_write, jsonable
from ..connectivity import connection_report, snap_report, apply_snap
from ..document import physical_context
from ..external import compare_bom
from ..geometry import analyze_geometry
from ..validation import validate_file, validate_text
from .base import Command, geometry_options, geometry_report, positive, scope_options


def add_build(commands):
    c = commands.add_parser("build", help="Build a validated MPD from a JSON plan")
    c.add_argument("plan")
    c.add_argument("--output", required=True)
    c.add_argument("--report")
    c.add_argument("--force", action="store_true", help="Replace an existing MPD after successful validation")
    geometry_options(c)


def run_build(args, library, parts):
    target = Path(args.output)
    if target.suffix.casefold() != ".mpd":
        raise ValueError("Output must end in .mpd")
    if target.exists() and not args.force:
        raise ValueError("Output exists; use --force to replace after successful validation")
    plan = load_plan(args.plan)
    text, model, diagnostics = build_plan(plan, parts, instance_limit=args.max_instances)
    geometry = None
    if not any(d["severity"] == "error" for d in diagnostics):
        geometry = geometry_report(model, parts, args)
        diagnostics += geometry["diagnostics"]
    passed = not any(d["severity"] == "error" for d in diagnostics)
    report = dict(profile="assembly", checks_passed=passed, physical_validity="not_proven", output=str(target),
                  written=passed, diagnostics=diagnostics, geometry=geometry)
    if passed:
        atomic_write(target, text)
    return report, 0 if passed else 1


CHECKS = ["validate", "inspect", "bom", "compare-bom", "snap", "connectors"]


def add_check(command):
    def register(commands):
        c = commands.add_parser(command)
        c.add_argument("file")
        c.add_argument("--report")
        scope_options(c)
        if command in {"validate", "inspect"}:
            geometry_options(c)
        if command == "compare-bom":
            c.add_argument("--csv", required=True, help="LeoCAD-exported BOM")
        if command == "validate":
            c.add_argument("--profile", choices=["assembly", "syntax"], default="assembly")
            c.add_argument("--geometry", action="store_true")
            c.add_argument("--strict", action="store_true", help="Warnings also fail (does not extend check coverage)")
        if command == "snap":
            c.add_argument("--moving", type=int, required=True)
            c.add_argument("--fixed", type=int, help="Fixed leaf index; omitted searches other occurrences")
            c.add_argument("--limit", type=positive, default=5)
            c.add_argument("--moving-depth", type=int, help="Move this ancestor of the moving leaf: 0 is outermost placement; omitted moves the leaf")
            c.add_argument("--moving-feature", help="Stable connector ID from connectors")
            c.add_argument("--fixed-feature", help="Stable connector ID from connectors")
            c.add_argument("--max-candidates", type=positive, default=100)
            c.add_argument("--max-instances", type=positive, default=100000)
            c.add_argument("--allow-occupied", action="store_true", help="Allow deliberate reuse of occupied interfaces, e.g. sliding bars")
            c.add_argument("--output", help="Apply a candidate to a new MPD after validation")
            c.add_argument("--candidate", type=int, default=0, help="Zero-based candidate to apply")
            c.add_argument("--force", action="store_true")
        if command == "connectors":
            c.add_argument("--occurrence", type=int, required=True)
            c.add_argument("--limit", type=positive, default=50)
            c.add_argument("--offset", type=int, default=0)
            c.add_argument("--max-instances", type=positive, default=100000)
    return register


def run_check(args, library, parts):
    assembly = args.command == "validate" and args.profile == "assembly"
    model, diagnostics = validate_file(args.file, parts, assembly=assembly, section=args.section, colour=args.colour,
                                       instance_limit=getattr(args,"max_instances",100000))
    report = dict(file=args.file, section=args.section, profile="assembly" if assembly else "syntax", checks_passed=False,
                  physical_validity="not_proven", diagnostics=diagnostics)
    if model and not any(d["severity"] == "error" for d in diagnostics):
        if args.command == "bom":
            model, parts = physical_context(model, parts)
            report["bom"] = jsonable(model.bill_of_materials(parts=parts))
            report["physical_placements"] = sum(row["quantity"] for row in report["bom"])
        elif args.command == "compare-bom":
            report["comparison"] = compare_bom(model,parts,args.csv)
            report["checks_passed"] = report["comparison"]["matches"]
            return report, 0 if report["checks_passed"] else 1
        elif args.command == "snap":
            result = snap_report(model, parts, args.moving, args.fixed, limit=args.limit,
                moving_depth=args.moving_depth, moving_feature=args.moving_feature, fixed_feature=args.fixed_feature,
                max_candidates=args.max_candidates, instance_limit=args.max_instances, allow_occupied=args.allow_occupied)
            report.update(result)
            if args.output:
                from ..builder import serialize_mpd
                target = Path(args.output)
                if target.resolve() == Path(args.file).resolve():
                    raise ValueError('Snap output must differ from the source file')
                if target.suffix.casefold() != '.mpd':
                    raise ValueError('Snap output must end in .mpd')
                if target.exists() and not args.force:
                    raise ValueError('Output exists; use --force')
                updated = apply_snap(model, result, args.candidate)
                text = serialize_mpd(updated)
                parsed, checked = validate_text(text, parts, assembly=True, instance_limit=args.max_instances)
                diagnostics.extend(checked)
                if not any(d['severity']=='error' for d in diagnostics):
                    geometry = analyze_geometry(parsed, parts, detail='summary', instance_limit=args.max_instances)
                    report['geometry'] = geometry
                    diagnostics.extend(geometry['diagnostics'])
                report.update(output=str(target), written=not any(d['severity']=='error' for d in diagnostics))
                if report['written']:
                    atomic_write(target, text)
            elif not result['candidates']:
                report.update(checks_passed=False, reason='No eligible verified snap candidates; inspect connector coverage, occupancy, and search limits')
                return report, 1
        elif args.command == "connectors":
            report['connections'] = connection_report(model, parts, args.occurrence, limit=args.limit,
                offset=args.offset, instance_limit=args.max_instances)
            if not report['connections']['complete']:
                report.update(checks_passed=False)
                return report, 1
        elif args.command == "inspect" or (args.command == "validate" and args.geometry):
            report["geometry"] = geometry_report(model, parts, args)
            diagnostics += report["geometry"]["diagnostics"]
        report["checks_passed"] = not any(d["severity"] == "error" for d in diagnostics)
    failed = not report["checks_passed"] or (getattr(args, "strict", False) and bool(diagnostics))
    return report, 1 if failed else 0


COMMANDS = {
    "build": Command(add_build, run_build),
    **{name: Command(add_check(name), run_check) for name in CHECKS},
}
