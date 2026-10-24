"""Role-coloured renders for a visual check of ldraw_tools.roles.

    python render_roles.py OUTDIR [--ref NAME ...] [--owner PATH ...] [--views home]

Every placement is recoloured by its role (legend below), the model is built and rendered with LeoCAD.
Writes OUTDIR/<name>.png, <name>.roles.json and summary.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from ldraw_tools import roles
from ldraw_tools.builder import build_plan
from ldraw_tools.common import get_parts, library_path, models_path
from ldraw_tools.editor import import_mpd
from ldraw_tools.external import render

PALETTE = {'roof': 4, 'wall': 19, 'window': 1, 'door': 70, 'base': 2, 'wheel': 14, 'tyre': 0, 'chassis': 72, 'body': 25,
           'glass': 11, 'wing': 22, 'engine': 26, 'trim': 15, 'detail': 13, 'structure': 71}


def recoloured(plan):
    import copy
    out = copy.deepcopy(plan)
    for s in out['sections']:
        for step in s['steps']:
            for p in step:
                p['colour'] = PALETTE.get(roles.canonical(p.get('role', 'structure')), 71)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('--ref', nargs='*', default=[])
    ap.add_argument('--owner', nargs='*', default=[])
    ap.add_argument('--views', nargs='*', default=['home'])
    ap.add_argument('--no-render', action='store_true')
    args = ap.parse_args(argv)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    parts = get_parts()
    facts = roles.PartFacts(parts)
    jobs = [(Path(n).stem, models_path() / n) for n in args.ref] + [(Path(p).stem, Path(p)) for p in args.owner]
    summary = {}
    for name, path in jobs:
        t0 = time.time()
        try:
            plan, warnings = import_mpd(path.read_text(encoding='utf-8', errors='ignore'), parts, name=path.name)
            hints = ' '.join(s['description'] for s in plan['sections'][:1])
            tagged, report = roles.tag_plan(plan, parts, hints=hints, facts=facts)
            report['seconds'] = round(time.time() - t0, 1)
            report['warnings'] = len(warnings)
            report['low_confidence'] = len(report['low_confidence'])
            (out / f'{name}.roles.json').write_text(json.dumps(report, indent=1))
            summary[name] = report
            if not args.no_render:
                text = build_plan(recoloured(tagged), parts, validate=False)[0]
                mpd = out / f'{name}.mpd'
                mpd.write_text(text)
                render(mpd, library_path(), out / name, views=args.views, timeout=240)
                mpd.unlink()
                for view in args.views:
                    (out / name / f'{view}.png').replace(out / f'{name}-{view}.png')
        except Exception as exc:  # noqa: BLE001
            summary[name] = dict(error=str(exc)[:300])
        print(name, summary[name].get('domain'), summary[name].get('counts'), summary[name].get('error', ''), flush=True)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
