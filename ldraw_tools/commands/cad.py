"""LeoCAD-backed checks, renders and GLB conversion."""

from pathlib import Path
from tempfile import TemporaryDirectory

from ..common import atomic_write
from ..document import physical_context, selected_source
from ..external import cad_check, prepare_glb, render
from ..validation import validate_file
from .base import Command, positive, scope_options


def add_cad_check(commands):
    c = commands.add_parser("cad-check", help="Run Python validation and a LeoCAD snapshot/BOM import check")
    c.add_argument("file")
    c.add_argument("--timeout", type=positive, default=90)
    scope_options(c)


def run_cad_check(args, library, parts):
    model, diagnostics = validate_file(args.file, parts, assembly=Path(args.file).suffix.casefold() == ".mpd", section=args.section, colour=args.colour)
    if model and not any(d["severity"] == "error" for d in diagnostics):
        with TemporaryDirectory(prefix="ldraw-section-") as tmp:
            source = args.file
            if args.section:
                text,_ = selected_source(args.file,args.section,args.colour)
                source = Path(tmp)/"section.mpd"
                atomic_write(source,text)
            report = cad_check(source, library, timeout=args.timeout)
        return dict(checks_passed=True, diagnostics=diagnostics, cad=report), 0
    return dict(checks_passed=False, diagnostics=diagnostics, cad=None), 1


def add_render(commands):
    c = commands.add_parser("render", help="Render review views and export a LeoCAD BOM")
    c.add_argument("file")
    c.add_argument("--outdir", required=True)
    c.add_argument("--views", nargs="+", default=["home", "top", "front"])
    c.add_argument("--timeout", type=positive, default=90)
    scope_options(c)


def run_render(args, library, parts):
    from ldraw import inspect_model
    from ldraw.lines import Line, OptionalLine, Triangle, Quadrilateral
    model, diagnostics = validate_file(args.file, parts, assembly=False, section=args.section, colour=args.colour)
    bounds = None
    if model and not any(d["severity"] == "error" for d in diagnostics):
        model, render_parts = physical_context(model, parts)
        raw_geometry = any(isinstance(obj, (Line, OptionalLine, Triangle, Quadrilateral))
                           for m in [model, *model.submodels.values()] for obj in m.objects)
        if not raw_geometry:
            inspection = inspect_model(model, render_parts)
            if inspection.complete:
                bounds = inspection.bounds
    with TemporaryDirectory(prefix="ldraw-section-") as tmp:
        source = args.file
        if args.section:
            text, _ = selected_source(args.file,args.section,args.colour)
            source = Path(tmp)/"section.mpd"
            atomic_write(source,text)
        report = render(source, library, args.outdir, views=args.views, timeout=args.timeout, bounds=bounds)
    report.update(source=args.file, section=args.section, source_diagnostics=diagnostics)
    return report, 0


def add_glb(commands):
    c = commands.add_parser("glb", help="Convert a local model or part with semantic descriptions")
    c.add_argument("file")
    c.add_argument("--output", required=True)
    c.add_argument("--timeout", type=positive, default=180)


def run_glb(args, library, parts):
    return prepare_glb(args.file, library, args.output, parts, timeout=args.timeout), 0


COMMANDS = {
    "cad-check": Command(add_cad_check, run_cad_check),
    "render": Command(add_render, run_render),
    "glb": Command(add_glb, run_glb),
}
