"""Find generated building, vehicle or detail examples."""

from .base import Command, positive


def add_examples(commands):
    c = commands.add_parser("examples", help="Find generated building, vehicle or detail examples")
    c.add_argument("query", nargs="?", default="")
    c.add_argument("--limit", type=positive, default=5)
    c.add_argument("--scale", choices=["minifigure", "microscale"])
    c.add_argument("--details", action="store_true")
    c.add_argument("--family", choices=["building", "vehicle", "reference", "technic", "mechanism", "spaceship"], default="building")


def run_examples(args, library, parts):
    from ..examples import search_examples
    if args.details and args.scale:raise ValueError('--scale applies to building examples')
    return search_examples(args.query,limit=args.limit,scale=args.scale,details=args.details,family=args.family),0


COMMANDS = {
    "examples": Command(add_examples, run_examples, needs_parts=False),
}
