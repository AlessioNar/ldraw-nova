"""Derive templates from models and write them as JSON.

    python derive_templates.py OUTDIR SPEC.json [--reports DIR] [--only ID ...] [--no-verify]

OUTDIR gets <id>.json (what ldraw_tools/templates loads); DIR (default OUTDIR) gets <id>.report.json: what was
verified, what was rejected and why, and how long it took.

SPEC.json: a list of {"id", "src" (reference model name or path), "title", "families", "kinds", "description"}
("family"/"kind" also accepted for one of each). Kinds are the words the fast path matches: a kind a generator
also has is a tie, and ties go to the generator.
"""
import argparse
import json
import time
from pathlib import Path

from ldraw_tools.common import get_parts, models_path
from ldraw_tools.templates import derive


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('spec')
    ap.add_argument('--no-verify', action='store_true')
    ap.add_argument('--reports')
    ap.add_argument('--only', nargs='*')
    args = ap.parse_args(argv)
    parts = get_parts()
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    reports = Path(args.reports or args.outdir)
    reports.mkdir(parents=True, exist_ok=True)
    for spec in json.loads(Path(args.spec).read_text()):
        tid, src, title = spec['id'], spec['src'], spec['title']
        if args.only and tid not in args.only:
            continue
        families = spec.get('families') or [spec['family']]
        kinds = spec.get('kinds') or [spec['kind']]
        path = Path(src) if Path(src).exists() else models_path() / src
        text = path.read_text(encoding='utf-8', errors='ignore')
        t0 = time.time()
        data, report = derive.derive_from_mpd(
            text, parts, name=path.name, id=tid, title=title, families=families, kinds=kinds,
            description=spec.get('description', title), verify=not args.no_verify, log=lambda *a: print('  ', *a, flush=True))
        report['seconds'] = round(time.time() - t0, 1)
        (out / f'{tid}.json').write_text(json.dumps(derive.compact(data), separators=(',', ':')), encoding='utf-8')
        (reports / f'{tid}.report.json').write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
        print(tid, report['leaves'], report['domain'], data['coverage'], report['seconds'], 's', flush=True)


if __name__ == '__main__':
    main()
