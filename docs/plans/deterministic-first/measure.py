"""Throwaway measurement driver (no LLM). Run inside the app image as the agent user:
   cd /opt/ldraw-nova && .venv/bin/python /tmp/measure.py CASE [CASE...]
Each case: make a plan deterministically, then build, validate(--geometry), render (CLI subprocesses,
the way an agent or the app would call them), recording wall time per phase to /tmp/w/CASE/timing.json.
"""
import json, os, subprocess, sys, time, copy
from pathlib import Path

AGENT = '/opt/ldraw-nova/ldraw-agent'
OUT = Path('/tmp/w')


def sh(args, **kw):
    t = time.time()
    p = subprocess.run(args, capture_output=True, text=True, **kw)
    return time.time() - t, p


def recolour(plan, mapping):
    """Deterministic recolour: replace colour codes / symbols everywhere in a plan."""
    s = json.dumps(plan)
    plan = json.loads(s)
    def walk(o):
        if isinstance(o, dict):
            if 'colour' in o and o['colour'] in mapping:
                o['colour'] = mapping[o['colour']]
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(plan)
    return plan


def car_red():
    from ldraw_tools import details, vehicle_details
    pals = details.palettes()
    p = copy.deepcopy(pals['heritage-racing'])
    p['roles'].update(body='@colours.Red', roof='@colours.Black', trim='@colours.Black')
    pals['red-car'] = p
    details.palettes = lambda: pals
    vehicle_details.palettes = lambda: pals
    from ldraw_tools.vehicles import vehicle_plan, DESIGNS
    return vehicle_plan('grand-tourer', 'red-car')


def truck():
    from ldraw_tools.vehicles import vehicle_plan
    return vehicle_plan('tipper-truck')


def pickup():
    from ldraw_tools.vehicles import vehicle_plan
    return vehicle_plan('pickup')


def cottage():
    # parametric house from the existing architecture helpers: shell + gable roof + porch
    from ldraw_tools.architecture import Module, room, gable, porch
    w, d = 8, 6
    scene = Module('cottage', 'Small cottage generated from room/gable helpers')
    shell = room('cottage-shell', w, d, body=19, trim=15, door=-2, windows=(2,), rows=6)
    roof = gable('cottage-roof', w, d, roof=4, gable_colour=19)
    scene.add(shell, 19, id='shell')
    scene.add(roof, 19, id='roof', attach={'to': 'shell', 'anchor': 'roof', 'using': 'base'})
    plan = scene.plan()
    plan['author'] = 'ldraw-nova det-first prototype'
    return plan


def starship():
    # retrieve + adapt the reviewed B-wing: deterministic recolour 71->white(15), 72->blue(1) on the built MPD
    return ('prebuilt', '/opt/ldraw-nova/examples/spaceship-atlas/b-wing/b-wing.mpd', {'71': '15', '72': '1'})


def suspension():
    return ('prebuilt', '/opt/ldraw-nova/examples/technic-atlas/mechanisms/independent-suspension/independent-suspension.mpd', {})


def box_chassis():
    from ldraw_tools.technic_recipes import structure_plan
    return structure_plan('service-platform', levels=2, colour=4, accent=14)[0]


CASES = dict(car_red=car_red, truck=truck, pickup=pickup, cottage=cottage, starship=starship,
             suspension=suspension, box_chassis=box_chassis)


def run(case, views):
    d = OUT / case
    d.mkdir(parents=True, exist_ok=True)
    rec = dict(case=case)
    t = time.time()
    plan = CASES[case]()
    if case == 'starship':
        # unknown colours in the source: report which colour codes it uses
        pass
    if isinstance(plan, tuple):
        _, src, cmap = plan
        out = []
        for l in open(src, encoding='utf8').read().splitlines():
            q = l.split()
            if len(q) > 14 and q[0] == '1' and q[1] in cmap:
                q[1] = cmap[q[1]]; l = ' '.join(q)
            out.append(l)
        (d / 'm.mpd').write_text('\r\n'.join(out) + '\r\n')
        rec['plan_s'] = round(time.time() - t, 2); rec['build_s'] = 0; rec['build_rc'] = 0
    else:
        (d / 'plan.json').write_text(json.dumps(plan))
        rec['plan_s'] = round(time.time() - t, 2)
        s, p = sh([AGENT, 'build', str(d / 'plan.json'), '--output', str(d / 'm.mpd'), '--force', '--detail', 'summary'])
        rec['build_s'] = round(s, 2); rec['build_rc'] = p.returncode
    if rec['build_rc']:
        rec['build_err'] = (p.stdout + p.stderr)[-1500:]
        (d / 'timing.json').write_text(json.dumps(rec, indent=1)); print(json.dumps(rec)); return
    s, p = sh([AGENT, 'validate', str(d / 'm.mpd'), '--geometry', '--detail', 'summary'])
    rec['validate_s'] = round(s, 2); rec['validate_rc'] = p.returncode
    try:
        v = json.loads(p.stdout)
        g = v['geometry']
        rec.update(checks_passed=v['checks_passed'], parts=g['occurrence_count'],
                   components=g.get('optimistic_component_count'),
                   diag=[(x['code'], x['severity']) for x in v['diagnostics']],
                   errors=[x['message'][:200] for x in v['diagnostics'] if x['severity'] == 'error'])
    except Exception as e:
        rec['validate_parse'] = str(e) + (p.stdout + p.stderr)[-800:]
    s, p = sh([AGENT, 'render', str(d / 'm.mpd'), '--outdir', str(d / 'r'), '--views', *views])
    rec['render_s'] = round(s, 2); rec['render_rc'] = p.returncode
    rec['render_views'] = views
    rec['total_s'] = round(sum(rec.get(k, 0) for k in ['plan_s', 'build_s', 'validate_s', 'render_s']), 2)
    (d / 'timing.json').write_text(json.dumps(rec, indent=1))
    print(json.dumps(rec))


if __name__ == '__main__':
    views = ['home', 'front', 'right', 'top']
    for c in sys.argv[1:]:
        run(c, views)
