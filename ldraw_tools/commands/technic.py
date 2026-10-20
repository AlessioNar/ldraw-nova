"""Reviewed Technic structural parts, plans and joint/brace checks."""

import json
from pathlib import Path

from ..builder import build_plan
from ..common import atomic_write, dumps
from ..document import selected_source
from ..geometry import analyze_geometry
from ..validation import validate_text
from .base import Command, positive, scope_options


def add_technic(commands):
    c = commands.add_parser('technic', help='Reviewed structural parts, plans and joint/brace checks for fixed structures')
    t = c.add_subparsers(dest='technic_command', required=True)
    t.add_parser('list', help='List editable structural recipes')
    d = t.add_parser('parts', help='Inspect reviewed nominal ports and geometry fingerprints')
    d.add_argument('code', nargs='?')
    d = t.add_parser('plan', help='Export a structural plan and required-joint contract')
    d.add_argument('name')
    d.add_argument('--levels', type=positive, default=2)
    d.add_argument('--colour', type=int, default=71)
    d.add_argument('--accent', type=int, default=14)
    d.add_argument('--output', required=True)
    d.add_argument('--force', action='store_true')
    d = t.add_parser('check', help='Review seated joints, restraints, required mounts and assembly order')
    d.add_argument('file')
    d.add_argument('--contract')
    d.add_argument('--report')
    d.add_argument('--max-instances', type=positive, default=500)
    scope_options(d)


def run_technic(args, library, parts):
    from ..technic import parts_report
    from ..technic_recipes import RECIPES, structure_plan
    from ..technic_review import review_structure
    if args.technic_command == 'list':
        return RECIPES, 0
    if args.technic_command == 'parts':
        report = parts_report(parts, args.code)
        return report, 0 if all(p['geometry_matches'] for p in report['parts']) else 1
    if args.technic_command == 'plan':
        target = Path(args.output)
        contract_path = target.with_suffix('.structure.json')
        if not args.force and (target.exists() or contract_path.exists()):
            raise ValueError('Output or structure contract exists; use --force')
        plan, contract = structure_plan(args.name, levels=args.levels, colour=args.colour, accent=args.accent)
        _, model, diagnostics = build_plan(plan, parts)
        review = review_structure(model, parts, contract=contract)
        diagnostics.extend(analyze_geometry(model, parts, detail='summary')['diagnostics'])
        if any(d['severity']=='error' for d in diagnostics) or not review['checks_passed']:
            return dict(written=False, diagnostics=diagnostics, structure=review), 1
        import hashlib
        contract['model_sha256'] = hashlib.sha256(model.to_ldraw().encode()).hexdigest()
        atomic_write(target, dumps(plan)+'\n')
        atomic_write(contract_path, dumps(contract)+'\n')
        return dict(plan=str(target), contract=str(contract_path), scope='technic-structure',
                    next='Build, validate, technic check with the contract, render and review insertion access.'), 0
    source, _ = selected_source(args.file, args.section, args.colour)
    model, diagnostics = validate_text(source, parts, assembly=True, instance_limit=args.max_instances)
    contract = json.loads(Path(args.contract).read_text()) if args.contract else None
    review = review_structure(model, parts, contract=contract, instance_limit=args.max_instances)
    review['source_diagnostics'] = diagnostics
    review['checks_passed'] &= not any(d['severity']=='error' for d in diagnostics)
    if args.report:
        atomic_write(Path(args.report), dumps(review)+'\n')
    return review, 0 if review['checks_passed'] else 1


COMMANDS = {
    "technic": Command(add_technic, run_technic),
}
