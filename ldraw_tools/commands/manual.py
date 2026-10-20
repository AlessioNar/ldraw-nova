"""Step manuals for source submodels and curated mechanisms."""

import json
from pathlib import Path

from ..common import ROOT
from .base import Command, positive


def add_manual(commands):
    c = commands.add_parser('manual', help='Study any source submodel through build pages and export it for an atlas')
    m = c.add_subparsers(dest='manual_command', required=True)
    d = m.add_parser('prepare', help='Extract a source assembly and make attributed step manuals')
    d.add_argument('file')
    d.add_argument('--section', required=True)
    d.add_argument('--outdir', required=True)
    d.add_argument('--title')
    d.add_argument('--notes', help='JSON study notes: lesson, construction, interfaces, parent_context, reuse_notes')
    d.add_argument('--views', nargs='+', default=['home','back'])
    d.add_argument('--colour', type=int, default=7)
    d.add_argument('--no-render', action='store_true')
    d.add_argument('--overview', action='store_true', help='Render completed-model views; keep source step data for selecting smaller studies')
    d.add_argument('--normalize-rotations', action='store_true')
    d.add_argument('--repair-bfc-comments', action='store_true')
    d.add_argument('--force', action='store_true')
    d.add_argument('--max-instances', type=positive, default=2000)
    d = m.add_parser('review', help='Record the build pages and final preview actually opened')
    d.add_argument('directory')
    d.add_argument('--images', nargs='+', required=True)
    d.add_argument('--note', required=True)
    d = m.add_parser('export', help='Copy a visually reviewed study and placement plan for adaptation')
    d.add_argument('reference', help='Prepared study directory')
    d.add_argument('--outdir', required=True)
    d.add_argument('--force', action='store_true')


def run_manual(args, library, parts):
    from ..manuals import prepare_manual, review_manual, export_manual
    if args.manual_command == 'prepare':
        notes = json.loads(Path(args.notes).read_text()) if args.notes else None
        report = prepare_manual(args.file, args.section, args.outdir, parts, title=args.title,
            views=args.views, colour=args.colour, notes=notes, renders=not args.no_render,
            normalize_rotations=args.normalize_rotations, repair_bfc=args.repair_bfc_comments,
            force=args.force, max_instances=args.max_instances, kind='construction', overview=args.overview)
        return report, 0 if report['source_checks_passed'] and report['bom_matches'] is not False else 1
    if args.manual_command == 'review':
        return review_manual(args.directory, images=args.images, note=args.note), 0
    return export_manual(args.reference, args.outdir, force=args.force), 0


def add_mechanism(commands):
    c = commands.add_parser('mechanism', help='Study and reuse mechanisms from source and build pages; no motion analysis')
    m = c.add_subparsers(dest='mechanism_command', required=True)
    m.add_parser('list', help='List the curated mechanism studies')
    d = m.add_parser('prepare', help='Extract a source assembly and make attributed step manuals')
    d.add_argument('file')
    d.add_argument('--section', required=True)
    d.add_argument('--outdir', required=True)
    d.add_argument('--title')
    d.add_argument('--operation', help='JSON notes: function, fixed, moving, input, output, parent_context, reuse_notes')
    d.add_argument('--views', nargs='+', default=['home','back'])
    d.add_argument('--colour', type=int, default=7)
    d.add_argument('--no-render', action='store_true')
    d.add_argument('--normalize-rotations', action='store_true')
    d.add_argument('--repair-bfc-comments', action='store_true')
    d.add_argument('--force', action='store_true')
    d.add_argument('--max-instances', type=positive, default=2000)
    d = m.add_parser('review', help='Record the build pages and final preview actually opened')
    d.add_argument('directory')
    d.add_argument('--images', nargs='+', required=True)
    d.add_argument('--note', required=True)
    d = m.add_parser('export', help='Copy a visually reviewed study and placement plan for adaptation')
    d.add_argument('reference', help='Curated mechanism key or prepared study directory')
    d.add_argument('--outdir', required=True)
    d.add_argument('--force', action='store_true')


def run_mechanism(args, library, parts):
    from ..manuals import prepare_manual, review_manual, export_manual
    if args.mechanism_command == 'list':
        from ..examples import search_examples
        return search_examples(family='mechanism', limit=100), 0
    if args.mechanism_command == 'prepare':
        notes = json.loads(Path(args.operation).read_text()) if args.operation else None
        report = prepare_manual(args.file, args.section, args.outdir, parts, title=args.title,
            views=args.views, colour=args.colour, notes=notes, renders=not args.no_render,
            normalize_rotations=args.normalize_rotations, repair_bfc=args.repair_bfc_comments,
            force=args.force, max_instances=args.max_instances)
        return report, 0 if report['source_checks_passed'] and report['bom_matches'] is not False else 1
    if args.mechanism_command == 'review':
        return review_manual(args.directory, images=args.images, note=args.note), 0
    reference = Path(args.reference)
    if not reference.is_dir():
        from ..discovery import confined
        reference = confined(ROOT/'examples/mechanism-atlas', args.reference)
    return export_manual(reference, args.outdir, force=args.force), 0


COMMANDS = {
    "manual": Command(add_manual, run_manual),
    "mechanism": Command(add_mechanism, run_mechanism),
}
