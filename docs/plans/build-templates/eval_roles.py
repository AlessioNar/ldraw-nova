"""Score ldraw_tools.roles against generator ground truth (per-placement roles stamped at construction).

    python docs/plans/build-templates/eval_roles.py [out.json]

Modes: geometry+parts only (submodel names ignored), + submodel names, + names and a family hint (what the
understanding step knows).
"""
import collections
import json
import sys
import time

from ldraw_tools import generators, roles
from ldraw_tools.common import get_parts

CASES = {
    'house': [{}, dict(width=12, depth=8, courses=8, windows=4, porch=True, chimney='left', dormer=True),
              dict(roof_style='flat', door_side='centre', wall_colour='white', trim_colour='white', roof_colour='white')],
    'starfighter': [{}, dict(length=24, wingspan=24, engines=4, wing_style='swept')],
    'grand-tourer': [{}], 'delivery-van': [{}], 'pickup': [{}], 'tipper-truck': [{}],
    'touring-motorcycle': [{}], 'harbour-launch': [{}], 'courier-jet': [{}],
    'arcade-bay': [{}], 'street-lantern': [{}],
}
HINT = {'house': 'house', 'starfighter': 'spaceship', 'grand-tourer': 'car', 'delivery-van': 'van', 'pickup': 'pickup truck',
        'tipper-truck': 'truck', 'touring-motorcycle': 'motorcycle', 'harbour-launch': 'boat', 'courier-jet': 'jet plane',
        'arcade-bay': 'building arcade', 'street-lantern': ''}


def truth(plan):
    sections = {s['name'].casefold() for s in plan['sections']}
    return {(s['name'], p['id']): roles.canonical(p['role']) for s in plan['sections'] for step in s['steps'] for p in step
            if p.get('role') and p['ref'].casefold() not in sections}


def strip(plan):
    out = json.loads(json.dumps(plan))
    for s in out['sections']:
        for step in s['steps']:
            for p in step:
                p.pop('role', None)
    return out


def main(out_path=None):
    parts = get_parts()
    facts = roles.PartFacts(parts)
    modes = {'parts+geometry': dict(use_names=False), '+submodel names': dict(use_names=True),
             '+names+family hint': dict(use_names=True, hint=True)}
    report = {m: dict(total=0, ok=0, by_gen=collections.defaultdict(lambda: [0, 0]), confusion=collections.Counter(),
                      by_role=collections.defaultdict(lambda: [0, 0]), calibration=collections.defaultdict(lambda: [0, 0]))
              for m in modes}
    for gid, cases in CASES.items():
        for params in cases:
            plan = generators.generate(gid, params)['plan']
            gold = truth(plan)
            for mode, opt in modes.items():
                t = time.time()
                found = roles.classify(strip(plan), parts, hints=HINT[gid] if opt.get('hint') else '',
                                       use_names=opt['use_names'], facts=facts)
                r = report[mode]
                for key, want in gold.items():
                    got = found.get(key)
                    got_role = got.role if got else 'missing'
                    hit = got_role == want
                    r['total'] += 1
                    r['ok'] += hit
                    r['by_gen'][gid][0] += hit
                    r['by_gen'][gid][1] += 1
                    r['by_role'][want][0] += hit
                    r['by_role'][want][1] += 1
                    if not hit:
                        r['confusion'][f'{want} -> {got_role}'] += 1
                    if got:
                        b = min(int(got.confidence * 5), 4)
                        r['calibration'][f'{b / 5:.1f}-{(b + 1) / 5:.1f}'][0] += hit
                        r['calibration'][f'{b / 5:.1f}-{(b + 1) / 5:.1f}'][1] += 1
    summary = {}
    for mode, r in report.items():
        summary[mode] = dict(
            accuracy=round(r['ok'] / r['total'], 3), placements=r['total'],
            by_generator={g: f'{a}/{b} = {a / b:.2f}' for g, (a, b) in sorted(r['by_gen'].items())},
            by_role={g: f'{a}/{b} = {a / b:.2f}' for g, (a, b) in sorted(r['by_role'].items())},
            top_confusions=r['confusion'].most_common(12),
            calibration={k: f'{a}/{b} = {a / b:.2f}' for k, (a, b) in sorted(r['calibration'].items())})
    print(json.dumps(summary, indent=1))
    if out_path:
        open(out_path, 'w').write(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
