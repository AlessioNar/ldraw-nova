"""Discovery index, search, reference cards, catalogs and reviews."""

import json
import sys
from pathlib import Path

from ..builder import build_plan
from ..common import atomic_write, dumps
from .base import Command, positive


def add_discover(commands):
    c = commands.add_parser("discover", help="Find, measure and visually review parts, source models and submodels")
    discovery = c.add_subparsers(dest="discovery_command", required=True)
    d = discovery.add_parser("index", help="Build a local typed index from the three Jev fields and actual source headers")
    d.add_argument("--refresh", action="store_true")
    d = discovery.add_parser("search", help="Rank eligible references, inspect contents and diversify the shortlist")
    d.add_argument("kind", choices=["parts", "models", "submodels"])
    d.add_argument("query")
    d.add_argument("--engine", choices=["auto", "jev", "fts"], default="auto",
                   help="auto uses Jev when jev-rerank and TYPESAFE_API_KEY are available or fails over to FTS (keyword/BM25), reporting engine_used")
    d.add_argument("--limit", type=positive, default=10)
    d.add_argument("--pool", type=positive, default=60)
    d.add_argument("--candidates", type=int, default=500, help="Jev candidate budget; 0 scores all eligible records")
    d.add_argument("--all-families", action="store_true", help="Include Technic and other product families")
    d.add_argument("--construction", choices=['system', 'all', 'technic-structure', 'mechanism'],
                   help="Construction scope; Technic structure filters the selected section and its BOM")
    d.add_argument("--min-parts", type=int, default=2)
    d.add_argument("--max-parts", type=positive)
    d.add_argument("--max-technic-share", type=float, default=0.5)
    d.add_argument("--parent-cap", type=positive, default=2)
    d.add_argument("--yes")
    d.add_argument("--no")
    d.add_argument("--report")
    d = discovery.add_parser("show", help="Resolve a stable identity and inspect its dependencies and BOM")
    d.add_argument("id")
    d = discovery.add_parser("recipe", help="List or export selected parameterized construction recipes")
    d.add_argument("name", nargs='?')
    d.add_argument("--height", type=int)
    d.add_argument("--colour", type=int)
    d.add_argument("--accent", type=int)
    d.add_argument("--output")
    d.add_argument("--force", action="store_true")
    d = discovery.add_parser("parts", help="Suggest actual parts from the constructions in a saved search report")
    d.add_argument("results")
    d.add_argument("--limit", type=positive, default=20)
    d = discovery.add_parser("prepare", help="Extract, measure and render one reference card")
    d.add_argument("id")
    d.add_argument("--outdir", required=True)
    d.add_argument("--views", nargs='+', default=["home","front","right","top"])
    d.add_argument("--colour", type=int, default=7)
    d.add_argument("--contacts", choices=["none","auto","all"], default="none")
    d.add_argument("--refresh", action="store_true")
    d = discovery.add_parser("catalog", help="Build a resumable visual catalog from a manifest or saved search")
    d.add_argument("manifest")
    d.add_argument("--outdir", required=True)
    d.add_argument("--views", nargs='+', default=["home","front","right","top"])
    d.add_argument("--contacts", choices=["none","auto","all"], default="none")
    d.add_argument("--refresh", action="store_true")
    d.add_argument("--jobs", type=int, choices=[1,2,3,4], default=1, help="Independent renderer processes; each owns separate CAD and geometry state")
    d = discovery.add_parser("review", help="Record the agent's visual review after opening the rendered images")
    d.add_argument("id")
    d.add_argument("--catalog", required=True)
    d.add_argument("--decision", choices=["reuse","adapt","technique","reject"], required=True)
    d.add_argument("--note", required=True)
    d.add_argument("--viewed-views", nargs='+', required=True, help="Only the rendered views you actually opened and inspected")
    d = discovery.add_parser("example", help="Export a reviewed reference as an editable example with a placement guide")
    d.add_argument("id")
    d.add_argument("--catalog", required=True)
    d.add_argument("--output", required=True)
    d.add_argument("--title", required=True)
    d.add_argument("--lesson", required=True)
    d.add_argument("--placement-notes", required=True)
    d.add_argument("--scale", choices=["minifigure","microscale","display","unknown"], default="unknown")
    d.add_argument("--force", action="store_true")


def run_discover(args, library, parts):
    from ..discovery import DiscoveryIndex, search, part_suggestions
    from ..reference_catalog import prepare_reference, build_catalog, export_example, record_review
    index = DiscoveryIndex(parts)
    command = args.discovery_command
    if command == 'recipe':
        from ..reference_recipes import RECIPES,recipe_plan
        if args.name is None:
            if args.output:raise ValueError('Choose a recipe name to export')
            return RECIPES,0
        plan=recipe_plan(args.name,height=args.height,colour=args.colour,accent=args.accent)
        _,_,diagnostics=build_plan(plan,parts)
        if any(d['severity']=='error' for d in diagnostics):return dict(written=False,diagnostics=diagnostics),1
        if args.output:
            target=Path(args.output)
            if target.exists() and not args.force:raise ValueError('Output exists; use --force')
            atomic_write(target,dumps(plan)+'\n')
        return dict(plan=plan,output=args.output,recipe=RECIPES[args.name],next='Build, inspect connections, render and review the adapted module.'),0
    if command == 'index':
        return index.ensure(force=args.refresh),0
    if command == 'parts':
        return part_suggestions(json.loads(Path(args.results).read_text())['results'],limit=args.limit),0
    if command == 'search':
        if args.all_families and args.construction not in {None, 'all'}:
            raise ValueError('Choose --construction or --all-families, not conflicting scopes')
        return search(index,args.kind,args.query,engine=args.engine,limit=args.limit,pool=args.pool,candidates=args.candidates,
                      system=not args.all_families,construction=args.construction,min_parts=args.min_parts,max_parts=args.max_parts,
                      max_technic_share=args.max_technic_share,parent_cap=args.parent_cap,yes=args.yes,no=args.no),0
    index.ensure()
    if command == 'show':
        row=index.get(args.id)
        return dict(row,inventory=index.inventory(row)),0
    if command == 'prepare':
        return prepare_reference(index,index.get(args.id),args.outdir,views=args.views,colour=args.colour,contacts=args.contacts,refresh=args.refresh),0
    if command == 'catalog':
        manifest=json.loads(Path(args.manifest).read_text())
        if 'references' not in manifest and 'results' in manifest:
            manifest=dict(title=manifest.get('query','Discovery shortlist'),references=[dict(id=r['id'],role=manifest.get('query','reference')) for r in manifest['results']])
        report=build_catalog(index,manifest,args.outdir,views=args.views,contacts=args.contacts,refresh=args.refresh,
                             jobs=args.jobs,
                             progress=lambda value:print(json.dumps(value),file=sys.stderr,flush=True))
        return report,1 if report['failures'] else 0
    if command == 'review':
        return record_review(index,args.id,args.catalog,decision=args.decision,note=args.note,viewed_views=args.viewed_views),0
    if command == 'example':
        return export_example(index,args.id,args.catalog,args.output,title=args.title,lesson=args.lesson,
                              placement_notes=args.placement_notes,scale=args.scale,force=args.force),0


COMMANDS = {
    "discover": Command(add_discover, run_discover),
}
