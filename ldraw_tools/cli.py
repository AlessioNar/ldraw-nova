from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys

import jsonschema
from ldraw.errors import PartError

from .commands import cad, design, discover, examples, library, manual, model, spaceship, study, technic, vehicle
from .commands.base import Command
from .common import atomic_write, dumps, get_parts, library_path

COMMANDS: dict[str, Command] = {}
for module in (examples, discover, spaceship, manual, technic, vehicle, library, design, study, model, cad):
    COMMANDS.update(module.COMMANDS)

# Registration order is the order subcommands appear in --help.
ORDER = ["examples", "discover", "spaceship", "manual", "mechanism", "technic", "vehicle", "catalog", "design",
         "part-board", "doctor", "index", "search", "part", "colours", "spec", "sections", "study", "extract",
         "matrix", "profiles", "build", "validate", "inspect", "bom", "compare-bom", "snap", "connectors",
         "cad-check", "render", "glb"]
assert set(ORDER) == set(COMMANDS), set(ORDER) ^ set(COMMANDS)


def parser():
    p = argparse.ArgumentParser(description="Generate, inspect and review LDraw MPD assemblies. All reports are JSON. See docs/agent/tooling.md.")
    p.add_argument("--library", help="LDraw parts library root (default LDRAW_DIR, then LDRAWDIR)")
    shadows = p.add_mutually_exclusive_group()
    shadows.add_argument("--shadow", action="append", help="LDCad directory/zip/csl; repeatable, replaces LDRAW_SHADOW or ./data/offLibShadow")
    shadows.add_argument("--no-shadow", action="store_true", help="Disable external shadow metadata")
    commands = p.add_subparsers(dest="command", required=True)
    for name in ORDER:
        COMMANDS[name].register(commands)
    return p


def run(args):
    command = COMMANDS[args.command]
    needs_parts = command.needs_parts(args) if callable(command.needs_parts) else command.needs_parts
    library = parts = None
    if needs_parts:
        library = library_path(args.library)
        parts = get_parts(library, refresh=args.command == "index", shadows=[] if args.no_shadow else args.shadow)
    return command.run(args, library, parts)


def main():
    args = parser().parse_args()
    try:
        report, status = run(args)
    except (OSError, ValueError, PartError, RecursionError, sqlite3.Error, subprocess.SubprocessError, jsonschema.ValidationError) as exc:
        report, status = dict(checks_passed=False, error=str(exc), error_type=type(exc).__name__), 2
    text = dumps(report) + "\n"
    if getattr(args, "report", None):
        atomic_write(args.report, text)
    print(text, end="")
    return status


if __name__ == "__main__":
    sys.exit(main())
