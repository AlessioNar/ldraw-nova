"""Shared pieces for the CLI command modules."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from ..geometry import analyze_geometry


@dataclass(frozen=True)
class Command:
    """One subcommand: how to register its arguments and how to run it.

    needs_parts is True when the parts library must be loaded before run, or a
    predicate on the parsed args; run(args, library, parts) then receives them.
    """
    register: Callable
    run: Callable
    needs_parts: bool | Callable = True


def positive(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def geometry_options(c):
    c.add_argument("--detail", choices=["summary", "full"], default="full")
    c.add_argument("--contacts", choices=["auto", "all", "none"], default="auto", help="auto computes contacts for at most 500 physical placements; scope larger models to sections")
    c.add_argument("--limit", type=positive, default=200, help="Maximum instance/contact/pair/component rows (does not limit checks)")
    c.add_argument("--offset", type=int, default=0, help="First physical occurrence to display")
    c.add_argument("--max-instances", type=positive, default=100000, help="Physical expansion budget")


def geometry_report(model, parts, args):
    if args.offset < 0:
        raise ValueError("--offset must be nonnegative")
    return analyze_geometry(model, parts, detail=args.detail, contacts=args.contacts,
                            output_limit=args.limit, pair_limit=args.limit, offset=args.offset, instance_limit=args.max_instances)


def scope_options(c):
    c.add_argument("--section", help="Inspect only this FILE block and its dependency closure; indices become section-local")
    c.add_argument("--colour", type=int, help="Explicit inherited colour for a selected subassembly")
