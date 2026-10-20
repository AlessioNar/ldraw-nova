"""Spaceship design briefs and reusable source constructions."""

from pathlib import Path

from ..common import atomic_write, dumps
from .base import Command


def add_spaceship(commands):
    c = commands.add_parser('spaceship', help='Advanced spacecraft design briefs and reusable source constructions')
    ships = c.add_subparsers(dest='spaceship_command', required=True)
    ships.add_parser('list', help='List whole-ship construction and inspiration studies')
    ships.add_parser('details', help='List reusable cockpit, wing, engine and hull studies')
    d = ships.add_parser('brief', help='Start a spaceship design brief with module and visual-review guidance')
    d.add_argument('archetype', choices=['starfighter','freighter','capital-ship'])
    d.add_argument('--output')
    d.add_argument('--force', action='store_true')
    d = ships.add_parser('export', help='Export a reviewed spaceship construction with source and placement plan')
    d.add_argument('name')
    d.add_argument('--outdir', required=True)
    d.add_argument('--force', action='store_true')


def run_spaceship(args, library, parts):
    from ..spaceships import design_brief, export_spaceship
    if args.spaceship_command in {'list','details'}:
        from ..examples import search_examples
        return search_examples(family='spaceship', details=args.spaceship_command == 'details', limit=100), 0
    if args.spaceship_command == 'brief':
        brief = design_brief(args.archetype)
        if args.output:
            target = Path(args.output)
            if target.exists() and not args.force:
                raise ValueError('Output exists; use --force')
            atomic_write(target, dumps(brief)+'\n')
        return brief, 0
    return export_spaceship(args.name, args.outdir, force=args.force), 0


COMMANDS = {
    "spaceship": Command(add_spaceship, run_spaceship),
}
