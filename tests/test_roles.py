"""Role classifier and per-placement generator roles (docs/plans/build-templates)."""
import hashlib
import json
from pathlib import Path


from ldraw_tools import generators, roles

HASHES = json.loads((Path(__file__).parent / 'fixtures' / 'generator_plan_hashes.json').read_text())


def placements(plan):
    return [p for s in plan['sections'] for step in s['steps'] for p in step]


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k != 'role'}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def test_generator_plans_are_unchanged_by_per_placement_roles():
    """Roles are an additive field: every generator plan, roles removed, hashes as it did before the change."""
    for case, row in HASHES.items():
        gid = case.split('#')[0]
        plan = generators.generate(gid, row['params'])['plan']
        digest = hashlib.sha1(json.dumps(strip(plan), sort_keys=True, default=str).encode()).hexdigest()
        assert digest == row['sha1'], case


def test_roles_follow_the_part_not_the_colour():
    """White walls, white trim, white door and white roof: the old colour-keyed tags collapsed them into one role."""
    plan = generators.generate('house', dict(wall_colour='white', trim_colour='white', roof_colour='white', door_colour='white',
                                             chimney='left', porch=True, windows=2))['plan']
    found = {p['role'] for p in placements(plan) if 'role' in p}
    assert {'wall', 'trim', 'roof', 'door', 'window', 'chimney', 'porch', 'base'} <= found
    stack = [p for p in placements(plan) if p['id'].startswith('chimney')]
    assert stack and {p['role'] for p in stack} == {'chimney'}


def test_one_colour_car_keeps_its_roles():
    plan = generators.generate('grand-tourer', dict(body='red', roof='red', trim='red', glass='trans black'))['plan']
    found = {p['role'] for p in placements(plan) if 'role' in p}
    assert {'body', 'roof', 'chassis', 'tyre', 'wheel'} <= found


def test_almost_every_placement_of_every_generator_has_a_role():
    for gid in generators.REGISTRY:
        plan = generators.generate(gid)['plan']
        tagged = [p for p in placements(plan) if p.get('role')]
        assert len(tagged) >= 0.7 * len(placements(plan)), gid


def test_part_kinds_from_descriptions():
    kinds = {'Slope Brick 45  2 x  2': 'slope', 'Window  1 x  4 x  3': 'window', 'Wheel Rim  6.4 x  8': 'wheel',
             'Tyre  6/ 50 x  8 Offset Tread': 'tyre', 'Brick  1 x  4': 'brick', 'Plate  2 x  4': 'plate',
             'Tile  1 x  2 with Groove': 'tile', 'Door  1 x  4 x  6 Frame': 'door', 'Windscreen  2 x  4 x  2': 'windscreen',
             'Baseplate 16 x 16': 'baseplate', 'Minifig Head': 'detail', 'Wing  6 x 12 Left': 'wing',
             'Plane Jet Engine Centre': 'engine', 'Glass for Window  1 x  2 x  2 without Sill': 'glass_window',
             '~Moved to 3040b': 'other'}
    for description, kind in kinds.items():
        assert roles.part_kind(description) == kind, description
    assert roles.slope_angle('Slope Brick 33  3 x  1') == 33


def test_classifier_recovers_house_roles_without_any_tags(official):
    plan = generators.generate('house', dict(chimney='left', porch=True))['plan']
    names = {x['name'].casefold() for x in plan['sections']}
    gold = {(s['name'], p['id']): roles.canonical(p['role']) for s in plan['sections'] for step in s['steps'] for p in step
            if p.get('role') and p['ref'].casefold() not in names}
    found = roles.classify(strip(plan), official, use_names=False)
    assert found.pop('_domain') == 'building'
    hits = sum(found[k].role == v for k, v in gold.items())
    assert hits / len(gold) >= 0.55
    for key, role in gold.items():       # the parts that name themselves are always right
        if role in ('window', 'door'):
            assert found[key].role == role, key


def test_classifier_vehicle_wheels(official):
    plan = generators.generate('pickup')['plan']
    found = roles.classify(strip(plan), official)
    assert found.pop('_domain') == 'vehicle'
    tyres = {found[(s['name'], p['id'])].role for s in plan['sections'] for step in s['steps'] for p in step
             if p['ref'] in ('6015.dat', '6014b.dat') and (s['name'], p['id']) in found}
    assert tyres == {'tyre', 'wheel'}


def test_tag_plan_keeps_roles_already_there(official):
    plan = generators.generate('house')['plan']
    edited = json.loads(json.dumps(plan))
    placements(edited)[0]['role'] = 'trim'
    out, report = roles.tag_plan(edited, official)
    assert placements(out)[0]['role'] == 'trim'
    assert report['domain'] == 'building'
    forced, _ = roles.tag_plan(edited, official, overwrite=True)
    assert placements(forced)[0]['role'] != 'trim'


def test_role_vocabulary_is_closed():
    assert set(roles.CANONICAL.values()) <= set(roles.ROLES)
    assert set(roles.ROLE_WORDS.values()) <= set(roles.ROLES)
