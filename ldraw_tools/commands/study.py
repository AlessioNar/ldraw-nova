"""Study an OMR assembly and extract dependency-closed sections."""

from pathlib import Path

from ..common import atomic_write, dumps
from ..document import extract_section, study_model
from ..validation import validate_text
from .base import Command, positive


def add_study(commands):
    c = commands.add_parser("study", help="Inventory an OMR assembly hierarchy, physical BOM, and source issues")
    c.add_argument("file")
    c.add_argument("--report")
    c.add_argument("--max-instances", type=positive, default=100000)
    c.add_argument("--detail", choices=["summary", "full"], default="summary")
    c.add_argument("--limit", type=positive, default=30, help="Summary section/diagnostic rows")


def run_study(args, library, parts):
    # An informational inventory is useful even when the reference has errors.
    report=study_model(args.file, parts, instance_limit=args.max_instances)
    report["detail"]=args.detail
    if args.detail=="summary":
        report.pop("bom")
        report["sections_truncated"]=len(report["sections"])>args.limit
        report["sections"]=[{k:v for k,v in s.items() if k in {"name","kind","direct_placements","physical_placements","scene_instances","reachable","steps"}}
                            for s in report["sections"][:args.limit]]
        report["diagnostics_truncated"]=len(report["diagnostics"])>args.limit
        report["diagnostics"]=report["diagnostics"][:args.limit]
    return report, 0


def add_extract(commands):
    c = commands.add_parser("extract", help="Copy one dependency-closed section with namespacing and attribution manifest")
    c.add_argument("file")
    c.add_argument("--section", required=True)
    c.add_argument("--namespace", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--repair-bfc-comments", action="store_true", help="Explicitly move annotation comments before INVERTNEXT, recording each edit")
    c.add_argument("--normalize-rotations", action="store_true", help="Project nearly rigid assembly matrices (error <=0.002) to proper rotations; record changes")
    c.add_argument("--force", action="store_true")


def run_extract(args, library, parts):
    target = Path(args.output)
    manifest_path = target.with_suffix(".manifest.json")
    if target.suffix.casefold() != ".mpd":
        raise ValueError("Output must end in .mpd")
    if target.resolve() == Path(args.file).resolve():
        raise ValueError("Extraction must not overwrite the source")
    if (target.exists() or manifest_path.exists()) and not args.force:
        raise ValueError("Output/manifest exists; use --force")
    text, manifest = extract_section(args.file,args.section,namespace=args.namespace,
                                    repair_bfc=args.repair_bfc_comments, normalize_rotations=args.normalize_rotations)
    _, diagnostics = validate_text(text, parts, assembly=False)
    manifest.update(syntax_checks_passed=not any(d["severity"] == "error" for d in diagnostics), diagnostics=diagnostics)
    # Extraction intentionally retains invalid source for review; never claims a validated build.
    atomic_write(target,text)
    atomic_write(manifest_path,dumps(manifest)+"\n")
    return dict(output=str(target),manifest=str(manifest_path),root=manifest["root"],
                written=True,checks_passed=manifest["syntax_checks_passed"],diagnostics=diagnostics,
                changes=len(manifest["changes"])), 0 if manifest["syntax_checks_passed"] else 1


COMMANDS = {
    "study": Command(add_study, run_study),
    "extract": Command(add_extract, run_extract),
}
