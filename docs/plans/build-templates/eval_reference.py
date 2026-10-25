"""Score ldraw_tools.roles on reference models, against the role their author's submodel names imply.

    python docs/plans/build-templates/eval_reference.py [out.json]

The reference library has no per-part role labels. What it has is submodels its authors named "Roof", "Door LH",
"Wing right", "Front wheel"... Every part inside such a submodel gets that name's role as a proxy label, and the
classifier is run WITHOUT submodel names (geometry and part descriptions only), so the names are not seen twice.
Parts outside a role-named submodel are not scored. The proxy is noisy in both directions (a "Roof" submodel also
holds the plates that carry the roof; a "Door" submodel holds its frame), so read the numbers as agreement with the
model's own author, not as accuracy against hand labels. Loose details (hoses, figures, lamps) are not scored.
"""
import collections
import json
import re
import sys
import time

from ldraw_tools import roles
from ldraw_tools.common import get_parts, models_path
from ldraw_tools.editor import import_mpd

# 24 models (System, not Technic): buildings, road and rail vehicles, aircraft and spaceships, with the domain we expect.
MODELS = {
    '31009-1.mpd': 'building', '31038-1_Automn.mpd': 'building', '4956-1_House-with-Arch.mpd': 'building',
    '4954-1_House-with-Truck.mpd': 'building', '4954-1_School.mpd': 'building', '4996-1_Cozy-Cafe.mpd': 'building',
    '5766-1.mpd': 'building', '5771-1.mpd': 'building', '5891-1_Holiday-House.mpd': 'building',
    '10183-1_Caboose.mpd': 'vehicle', '10183-1_Tram-2.mpd': 'vehicle', '10183-1_Passenger-car.mpd': 'vehicle',
    '4432-1.mpd': 'vehicle', '4993-1_Truck.mpd': 'vehicle', '5867-1.mpd': 'vehicle', '5767-1.mpd': 'vehicle',
    '60018-1.mpd': 'vehicle', '744-1_Truck.mpd': 'vehicle',
    '31045-1_Aircraft.mpd': 'aircraft', '31049-1_Single-Engine-Airplane.mpd': 'aircraft', '31092-1.mpd': 'aircraft',
    '6745-1_Harrier-Jet.mpd': 'aircraft', '6891-1.mpd': 'aircraft', '5892-1.mpd': 'aircraft',
}

LABELS = [   # submodel name -> proxy role; first match wins, `None` means "do not score parts in here"
    (None, r'cockpit|hood|bonnet|cover|support|hinge|holder|bracket|seat|pilot|minifig|figure|opener|joint|box|base'),
    ('window', r'window'), ('door', r'\bdoors?\b|hatch|garage'), ('roof', r'roof'),
    ('wheel', r'wheel|tyre|tire'), ('wing', r'\bwings?\b'), ('engine', r'engine|thruster|exhaust'),
    ('glass', r'windscreen|windshield|canopy'), ('chassis', r'chassis|axle'), ('wall', r'\bwall'),
]
_LABELS = [(r, re.compile(p)) for r, p in LABELS]
FOLD = {'tyre': 'wheel'}         # the proxy cannot tell a tyre from its rim


def proxy(chain, root):
    """Role implied by the innermost role-named submodel above a part, else None."""
    for section, _ in reversed(chain):
        if section == root:
            continue
        name = re.sub(r'\.(ldr|mpd|dat)$', '', section.casefold()).split(' - ')[-1]
        for role, pattern in _LABELS:
            if pattern.search(name):
                return role, name
            # keep looking outwards only when the name says nothing
    return None


def main(out_path=None):
    parts = get_parts()
    facts = roles.PartFacts(parts)
    by_role = collections.defaultdict(lambda: [0, 0])
    confusion = collections.Counter()
    per_model, domains = {}, {}
    ok = total = 0
    for name, expected in MODELS.items():
        t0 = time.time()
        path = models_path() / name
        plan, _ = import_mpd(path.read_text(encoding='utf-8', errors='ignore'), parts, name=path.name)
        found = roles.classify(plan, parts, use_names=False, facts=facts)
        domain = found.get('_domain')
        root = plan['sections'][0]['name']
        hit = n = 0
        seen = set()
        for leaf in roles.flatten(plan, parts, facts):
            label = proxy(leaf['chain'], root)
            key = roles._entry_key(plan, leaf['key'])
            if not label or label[0] is None or key in seen or roles.part_kind(leaf['facts']['desc']) in ('detail', 'light'):
                continue        # a hose or a minifig in a "Wing" submodel is still a loose detail
            seen.add(key)
            want = label[0]
            got = found.get(key)
            got_role = FOLD.get(got.role, got.role) if got else 'missing'
            good = got_role == want
            hit += good
            n += 1
            by_role[want][0] += good
            by_role[want][1] += 1
            if not good:
                confusion[f'{want} -> {got_role}'] += 1
        ok += hit
        total += n
        domains[name] = dict(expected=expected, got=domain)
        per_model[name] = dict(scored=n, agree=f'{hit}/{n} = {hit / n:.2f}' if n else 'n/a', domain=domain,
                               seconds=round(time.time() - t0, 1))
        print(name, per_model[name], flush=True)
    summary = dict(
        agreement=round(ok / total, 3), scored_parts=total, models=len(MODELS),
        domain_correct=f"{sum(d['expected'] == d['got'] for d in domains.values())}/{len(domains)}",
        domain_misses={k: v for k, v in domains.items() if v['expected'] != v['got']},
        by_role={r: f'{a}/{b} = {a / b:.2f}' for r, (a, b) in sorted(by_role.items())},
        top_confusions=confusion.most_common(12), per_model=per_model)
    print(json.dumps({k: v for k, v in summary.items() if k != 'per_model'}, indent=1))
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
