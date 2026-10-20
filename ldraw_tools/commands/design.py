"""Role-based palettes and reusable architectural detail plans."""

from pathlib import Path

from ..common import atomic_write, dumps
from .base import Command


def add_design(commands):
    c = commands.add_parser("design", help="Role-based palettes and reusable architectural detail plans")
    c.add_argument("kind", choices=["palettes", "details"])
    c.add_argument("name", nargs="?")
    c.add_argument("--palette", default="botanical-bookshop")
    c.add_argument("--output", help="Write a detail JSON plan")
    c.add_argument("--force", action="store_true")


def run_design(args, library, parts):
    from ..details import RECIPES, detail_plan, palette_report
    if args.kind == 'palettes':
        if args.output:raise ValueError('--output is for detail plans')
        return palette_report(parts,args.name), 0
    if not args.name:
        if args.output:raise ValueError('Choose a detail name to export')
        return RECIPES, 0
    plan=detail_plan(args.name,args.palette)
    if args.output:
        if Path(args.output).exists() and not args.force:raise ValueError('Output exists; use --force')
        atomic_write(args.output,dumps(plan)+'\n')
    return dict(plan=plan,output=args.output,review='Build, inspect and render this detail before placing it; reserve its envelope at the interface.'), 0


COMMANDS = {
    "design": Command(add_design, run_design),
}
