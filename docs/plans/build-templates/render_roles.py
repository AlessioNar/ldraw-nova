"""Role-coloured renders for a visual check of ldraw_tools.roles.

    python render_roles.py OUTDIR [--ref NAME ...] [--owner PATH ...] [--gen ID ...] [--views home] [--no-render]

Every placement is recoloured by its role (PALETTE below), the model is built and rendered with LeoCAD (snap.py).
Roles come from the classifier with submodel names (what a person's build gets); for --gen the generator's own
roles are removed first, so the picture shows what the classifier finds on a generated model.
Writes OUTDIR/<name>-<view>.png, <name>.roles.json and summary.json.
"""
import argparse
import json
import time
from pathlib import Path

from snap import snap

from ldraw_tools import generators, roles
from ldraw_tools.builder import build_plan
from ldraw_tools.common import get_parts, library_path, models_path
from ldraw_tools.editor import import_mpd

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
    ap.add_argument('--gen', nargs='*', default=[])
    ap.add_argument('--views', nargs='*', default=['home'])
    ap.add_argument('--no-render', action='store_true')
    args = ap.parse_args(argv)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    parts = get_parts()
    facts = roles.PartFacts(parts)
    jobs = [(Path(n).stem, models_path() / n) for n in args.ref] + [(Path(p).stem, Path(p)) for p in args.owner]
    jobs += [(f'generator-{g}', g) for g in args.gen]
    summary = {}
    for name, path in jobs:
        t0 = time.time()
        try:
            if isinstance(path, str):          # a generator: its plan without the roles it stamped
                plan, warnings = generators.generate(path)['plan'], []
                for section in plan['sections']:
                    for step in section['steps']:
                        for entry in step:
                            entry.pop('role', None)
            else:
                plan, warnings = import_mpd(path.read_text(encoding='utf-8', errors='ignore'), parts, name=path.name)
            hints = ' '.join(s.get('description', '') for s in plan['sections'][:1])
            tagged, report = roles.tag_plan(plan, parts, hints=hints, facts=facts)
            report['seconds'] = round(time.time() - t0, 1)
            report['warnings'] = len(warnings)
            report['low_confidence'] = len(report['low_confidence'])
            (out / f'{name}.roles.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
            summary[name] = report
            if not args.no_render:
                text = build_plan(recoloured(tagged), parts, validate=False)[0]
                mpd = out / f'{name}.mpd'
                mpd.write_text(text, encoding='utf-8')
                for view in args.views:
                    snap(mpd, out / f'{name}-{view}.png', library_path(), view=view)
                mpd.unlink()
        except Exception as exc:  # noqa: BLE001
            summary[name] = dict(error=str(exc)[:300])
        print(name, summary[name].get('domain'), summary[name].get('counts'), summary[name].get('error', ''), flush=True)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
