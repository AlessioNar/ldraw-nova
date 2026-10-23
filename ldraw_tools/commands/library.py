"""Library lookups: doctor, part index/search/inspection, colours, specification, sections, matrix and profiles."""

import json
import shutil
import sys
from importlib.metadata import version

from ..builder import rotation
from ..common import ROOT, DATA, database_path, jsonable, library_path, models_path, shadow_paths
from ..connectivity import metadata_summary
from ..geometry import profiles
from ..resources import search_spec, search_models, model_sections
from .base import Command, positive


def add_catalog(commands):
    c = commands.add_parser("catalog", help="Search the supplied part/colour categories using descriptive symbols")
    c.add_argument("kind", choices=["categories", "parts", "colours"])
    c.add_argument("query", nargs="?", default="")
    c.add_argument("--category")
    c.add_argument("--limit", type=positive, default=12)
    c.add_argument("--max-size", type=float, nargs=3, metavar=("X","Y","Z"), help="Maximum cached full bounds in LDU; not stacking dimensions")
    c.add_argument("--include-unavailable", action="store_true", help="Include missing, alias and internal entries with status flags")
    c.add_argument("--measure", action="store_true", help="Measure selected part results and flag differences from cached dimensions")


def run_catalog(args, library, parts):
    from ..catalog import search_catalog
    return search_catalog(parts,args.kind,args.query,category=args.category,limit=args.limit,
                          include_unavailable=args.include_unavailable,max_size=args.max_size,measure=args.measure), 0


def add_part_board(commands):
    c = commands.add_parser("part-board", help="Render 1–12 real part candidates into an offline visual shortlist")
    c.add_argument("refs", nargs='+')
    c.add_argument("--outdir", required=True)
    c.add_argument("--colour", default='19', help="Installed colour code or @colours.Name")
    c.add_argument("--timeout", type=positive, default=90)


def run_part_board(args, library, parts):
    from ..boards import part_board
    return part_board(args.refs,parts,library,args.outdir,colour=args.colour,timeout=args.timeout), 0


def add_doctor(commands):
    commands.add_parser("doctor", help="Show dependencies and source paths")


def run_doctor(args, library, parts):
    library = library_path(args.library)
    report = dict(python=sys.version.split()[0], packages={n: version(n) for n in ["pyldraw3", "numpy", "jsonschema"]},
                  library=str(library), library_present=(library / "parts").is_dir(),
                  models=str(models_path()), database=str(database_path()), pdf=str(ROOT / "docs/ldraw-specs.pdf"),
                  shadow_sources=jsonable(shadow_paths([] if args.no_shadow else args.shadow)),
                  tools={t: shutil.which(t) for t in ["pdftotext", "leocad", "jev-rerank", "mpd2glb.sh", "ldraw-render-steps.sh"]})
    return report, 0 if report["library_present"] and report["tools"]["pdftotext"] else 2


def add_index(commands):
    commands.add_parser("index", help="Refresh the local parts index; source library remains unchanged")


def run_index(args, library, parts):
    return dict(parts=len(parts.by_code), library=str(library), index=str(parts.path)), 0


def add_search(commands):
    c = commands.add_parser("search", help="Search actual library parts or annotated models")
    c.add_argument("kind", choices=["parts", "models", "submodels"])
    c.add_argument("query")
    c.add_argument("--limit", type=positive, default=10)
    c.add_argument("--offset", type=int, default=0, help="Model/submodel FTS page offset")


def search_needs_parts(args):
    return args.kind == "parts"


def run_search(args, library, parts):
    if args.kind != "parts":
        return search_models(args.query, limit=args.limit, submodels=args.kind == "submodels", offset=args.offset), 0
    # Match all plain terms; retain original filenames and descriptions.
    query = args.query.casefold().split()
    matches = [dict(code=c + ".dat", description=d) for c, d in parts.by_code.items()
               if all(t in (c + ".dat " + d).casefold() for t in query)]
    matches.sort(key=lambda r: (r["code"].casefold().removesuffix(".dat") != args.query.casefold().removesuffix(".dat"),
                                r["description"].startswith(("~", "=")), len(r["description"]), r["code"]))
    return dict(total=len(matches), results=matches[:args.limit]), 0


def add_part(commands):
    c = commands.add_parser("part", help="Inspect real parts' metadata, local bounds and connectors")
    c.add_argument("code", nargs="+", help="One part code, or several: the library loads once for all of them "
                                           "(a process start costs seconds, so look many parts up in one call)")
    c.add_argument("--limit", type=positive, default=30, help="Connector output limit (total is always reported)")
    c.add_argument("--brief", action="store_true", help="Only code, description, bounds, size, stud and connector counts "
                                                         "(the default when several codes are given)")
    c.add_argument("--full", action="store_true", help="Full detail for every code of a multi-part call")


def part_report(code, parts, limit):
    from ..catalog import resolve_part
    code = (resolve_part(code, parts) if code.startswith('@') else code).casefold().removesuffix(".dat")
    part = parts.part(code=code)
    g = parts.geometry(code)
    return dict(code=code + ".dat", path=str(part.path.resolve()), metadata=jsonable(part.metadata),
                complete=g.complete, bounds=jsonable(g.bounds), size=jsonable(g.bounds.size) if g.bounds else None,
                stud_positions=[jsonable(s.position) for s in g.top_studs],
                connector_count=len(g.connections), connectors=jsonable(g.connections[:limit]),
                connectors_truncated=len(g.connections) > limit, diagnostics=jsonable(g.diagnostics),
                connection_metadata=metadata_summary(g.connection_metadata),
                rectangular_profile=profiles().get(code),
                note="All coordinates are local LDU. Connector inference is evidence; inspect uncertain fits.")


def brief_part(report):
    metadata = report["metadata"] if isinstance(report["metadata"], dict) else {}
    return dict(code=report["code"], description=metadata.get("description"), complete=report["complete"],
                bounds=report["bounds"], size=report["size"], stud_count=len(report["stud_positions"]),
                connector_count=report["connector_count"])


def run_part(args, library, parts):
    if len(args.code) == 1 and not args.brief:
        report = part_report(args.code[0], parts, args.limit)           # one code, full detail: the original output
        return report, 0 if report["complete"] else 1
    reports, errors = [], {}
    for code in args.code:
        try:
            report = part_report(code, parts, args.limit)
        except Exception as exc:  # noqa: BLE001 - one unknown part must not hide the answers for the others
            errors[code] = str(exc)
            continue
        reports.append(report if args.full and not args.brief else brief_part(report))
    incomplete = [r["code"] for r in reports if not r["complete"]]      # reported, not a failure of the lookup itself
    return dict(count=len(reports), parts=reports, errors=errors, incomplete=incomplete), 0 if reports and not errors else 1


def add_colours(commands):
    c = commands.add_parser("colours", help="Look up codes in the installed LDConfig.ldr")
    c.add_argument("query", nargs="?", default="")


def run_colours(args, library, parts):
    return [jsonable(c) for k, c in sorted(parts.colours_by_code.items())
            if args.query.casefold() in (str(k) + " " + (c.name or "")).casefold()], 0


def add_spec(commands):
    c = commands.add_parser("spec", help="Search the mandatory PDF or retrieve a 1-based page")
    c.add_argument("query", nargs="?")
    c.add_argument("--page", type=positive)
    c.add_argument("--limit", type=positive, default=8)


def run_spec(args, library, parts):
    return search_spec(args.query, args.page, args.limit), 0


def add_sections(commands):
    c = commands.add_parser("sections", help="Read original annotated model sections with source line numbers")
    c.add_argument("file")
    c.add_argument("--section")


def run_sections(args, library, parts):
    return model_sections(args.file, args.section), 0


def add_matrix(commands):
    c = commands.add_parser("matrix", help="Compute a right-handed rotation in LDraw coordinates")
    c.add_argument("axis", choices=["x", "y", "z"])
    c.add_argument("degrees", type=float)


def run_matrix(args, library, parts):
    return dict(axis=args.axis, degrees=args.degrees, matrix=jsonable(rotation(args.axis, args.degrees))), 0


def add_profiles(commands):
    commands.add_parser("profiles", help="List curated ordinary brick/plate dimensions for on placement")


def run_profiles(args, library, parts):
    return json.loads((DATA / "rectangular-parts.json").read_text()), 0


COMMANDS = {
    "catalog": Command(add_catalog, run_catalog),
    "part-board": Command(add_part_board, run_part_board),
    "doctor": Command(add_doctor, run_doctor, needs_parts=False),
    "index": Command(add_index, run_index),
    "search": Command(add_search, run_search, needs_parts=search_needs_parts),
    "part": Command(add_part, run_part),
    "colours": Command(add_colours, run_colours),
    "spec": Command(add_spec, run_spec, needs_parts=False),
    "sections": Command(add_sections, run_sections, needs_parts=False),
    "matrix": Command(add_matrix, run_matrix, needs_parts=False),
    "profiles": Command(add_profiles, run_profiles, needs_parts=False),
}
