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


SPECIFIC_GOLD = {'roof', 'window', 'door', 'wheel', 'wing', 'engine', 'chassis', 'glass', 'base'}


def reference_models(limit=30, low=40, high=500):
    """Reference models whose submodel names carry a specific role word, spread over the library (every k-th)."""
    import re
    from ldraw_tools.common import models_path
    names = re.compile(r'^0 FILE (.*)$')
    found = []
    for path in sorted(models_path().glob('*.mpd')):
        text = path.read_text(encoding='utf-8', errors='ignore')
        n = sum(1 for line in text.splitlines() if line.startswith('1 '))
        sections = [m.group(1) for m in map(names.match, text.splitlines()) if m]
        hits = {roles._name_role(re.sub(r'\.(ldr|mpd|dat)$', '', x.casefold()).replace('-', ' ').replace('_', ' ')) for x in sections[1:]}
        if low <= n <= high and (hits & SPECIFIC_GOLD):
            found.append(path)
    step = max(len(found) // limit, 1)
    return found[::step][:limit], len(found)


def evaluate_references(limit=30, out_path=None):
    """Weak gold for reference models: the role word in the innermost submodel name above a part (roof, wheel, window...).

    The classifier runs with use_names=False, so the labels are independent of what it looked at. Only parts inside a
    submodel whose name says one of the specific roles are scored.
    """
    import re
    from ldraw_tools.editor import import_mpd
    parts = get_parts()
    facts = roles.PartFacts(parts)
    files, available = reference_models(limit)
    by_role = collections.defaultdict(lambda: [0, 0])
    confusion = collections.Counter()
    parts_missed = collections.Counter()
    per_model, total, ok = {}, 0, 0
    for path in files:
        plan, _ = import_mpd(path.read_text(encoding='utf-8', errors='ignore'), parts, name=path.name)
        found = roles.classify(plan, parts, hints='', use_names=False, facts=facts)
        sections = {x['name'] for x in plan['sections']}
        root = plan['sections'][0]['name']
        hit_n = n = 0
        for leaf in roles.flatten(plan, parts, facts):
            gold = None
            for section, _ in reversed(leaf['chain']):
                if section != root and section in sections:
                    gold = roles._name_role(re.sub(r'\.(ldr|mpd|dat)$', '', section.casefold()).replace('-', ' ').replace('_', ' '))
                    break
            if gold not in SPECIFIC_GOLD:
                continue
            got = found.get(leaf['key'])
            got_role = got.role if got else 'missing'
            hit = got_role == gold or (gold == 'wheel' and got_role == 'tyre')       # a wheel submodel holds the tyres too
            n += 1
            hit_n += hit
            by_role[gold][0] += hit
            by_role[gold][1] += 1
            if not hit:
                confusion[f'{gold} -> {got_role}'] += 1
                parts_missed[(gold, got_role, facts.get(leaf['ref'])['kind'], facts.get(leaf['ref'])['desc'][:36])] += 1
        total += n
        ok += hit_n
        per_model[path.name] = f'{hit_n}/{n}'
    summary = dict(models=len(files), eligible_models=available, scored_parts=total, accuracy=round(ok / max(total, 1), 3),
                   by_role={r: f'{a}/{b} = {a / b:.2f}' for r, (a, b) in sorted(by_role.items())},
                   top_confusions=confusion.most_common(15),
                   top_missed_parts=[[list(k), v] for k, v in parts_missed.most_common(40)], per_model=per_model)
    print(json.dumps(summary, indent=1))
    if out_path:
        open(out_path, 'w').write(json.dumps(summary, indent=1))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--reference':
        evaluate_references(int(sys.argv[2]) if len(sys.argv) > 2 else 30, sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else None)
