"""Vehicle designs, wheels, dedicated fittings and interface review."""

from pathlib import Path

from ..builder import build_plan
from ..common import atomic_write, dumps
from ..validation import validate_file
from .base import Command, positive, scope_options


def add_vehicle(commands):
    c = commands.add_parser("vehicle", help="System road, motorcycle, boat and aircraft designs, dedicated fittings and review")
    vehicle_commands = c.add_subparsers(dest="vehicle_command", required=True)
    vehicle_commands.add_parser("list", help="List editable vehicle starting points")
    v = vehicle_commands.add_parser("wheels", help="Inspect measured, matched wheel packs")
    v.add_argument("name", nargs="?")
    v = vehicle_commands.add_parser("details", help="List or export dedicated vehicle fitting recipes")
    v.add_argument("name", nargs="?")
    v.add_argument("--palette", default="heritage-racing")
    v.add_argument("--output", help="Write an editable detail JSON plan")
    v.add_argument("--force", action="store_true")
    v = vehicle_commands.add_parser("plan", help="Export an editable vehicle plan and its design brief")
    v.add_argument("name")
    v.add_argument("--palette", help="Vehicle palette (default depends on design)")
    v.add_argument("--output", required=True)
    v.add_argument("--force", action="store_true")
    v = vehicle_commands.add_parser("check", help="Review supported vehicle interfaces in a selected family profile")
    v.add_argument("file")
    v.add_argument("--profile", choices=["road", "motorcycle", "watercraft", "aircraft"], default="road")
    v.add_argument("--ground-y", type=float, default=0)
    v.add_argument("--report")
    v.add_argument("--limit", type=positive, default=100)
    v.add_argument("--max-instances", type=positive, default=100000)
    scope_options(v)


def run_vehicle(args, library, parts):
    from ..vehicles import DESIGNS, vehicle_plan, wheel_report, design_brief
    if args.vehicle_command == "list":
        return DESIGNS, 0
    if args.vehicle_command == "wheels":
        return wheel_report(parts, args.name), 0
    if args.vehicle_command == "details":
        from ..vehicle_details import DETAILS, detail_plan
        if not args.name:
            if args.output:raise ValueError('Choose a vehicle detail name to export')
            return DETAILS, 0
        plan = detail_plan(args.name, args.palette)
        _, _, diagnostics = build_plan(plan, parts)
        if any(d['severity']=='error' for d in diagnostics):
            return dict(checks_passed=False, written=False, diagnostics=diagnostics), 1
        if args.output:
            target = Path(args.output)
            if target.suffix.casefold() != '.json':raise ValueError('Plan output must end in .json')
            if target.exists() and not args.force:raise ValueError('Detail plan exists; use --force')
            atomic_write(target, dumps(plan)+'\n')
        return dict(recipe=DETAILS[args.name], plan=plan, output=args.output, diagnostics=diagnostics), 0
    if args.vehicle_command == "plan":
        target = Path(args.output)
        if target.suffix.casefold() != '.json':
            raise ValueError('Plan output must end in .json')
        brief = target.with_name(target.stem+'.brief.json')
        if not args.force and (target.exists() or brief.exists()):
            raise ValueError('Plan/brief exists; use --force')
        plan = vehicle_plan(args.name, args.palette)
        # Resolve and validate before exporting; standard build still does
        # the full geometry/contact pass on the saved editable plan.
        _, _, diagnostics = build_plan(plan, parts)
        if any(d['severity']=='error' for d in diagnostics):
            return dict(checks_passed=False, written=False, diagnostics=diagnostics), 1
        atomic_write(target, dumps(plan)+'\n')
        atomic_write(brief, dumps(design_brief(args.name, args.palette))+'\n')
        return dict(plan=str(target), brief=str(brief), diagnostics=diagnostics,
                    next='Build, validate, vehicle check, render and open all review views.'), 0
    from ..vehicle_review import review_vehicle
    model, diagnostics = validate_file(args.file, parts, assembly=True, section=args.section,
                                       colour=args.colour, instance_limit=args.max_instances)
    if model is None or any(d['severity']=='error' for d in diagnostics):
        return dict(checks_passed=False, diagnostics=diagnostics), 1
    report = review_vehicle(model, parts, profile=args.profile, ground_y=args.ground_y, limit=args.limit,
                            instance_limit=args.max_instances)
    report['diagnostics'] = diagnostics+report['diagnostics']
    report.update(file=args.file, section=args.section)
    return report, 0 if report['checks_passed'] else 1


COMMANDS = {
    "vehicle": Command(add_vehicle, run_vehicle),
}
