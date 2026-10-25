"""Generator registry: interface, colour names, the digest budget, and a corner sweep of every parameter range."""
import itertools
import json
import os
import random

import jsonschema
import pytest

from ldraw_tools import cli, generators
from ldraw_tools.builder import build_plan
from ldraw_tools.catalog import catalog_data
from ldraw_tools.generators.colours import NAMES, colour_code
from ldraw_tools.generators.vehicles import DESIGNS
from ldraw_tools.geometry import analyze_geometry
from ldraw_tools.vehicle_review import review_vehicle

IDS = list(generators.REGISTRY)
ROAD = [n for n, d in DESIGNS.items() if d.get('profile', 'road') == 'road']
PROFILES = {n: d.get('profile', 'road') for n, d in DESIGNS.items()}


def errors(diagnostics):
    return [d for d in diagnostics if d['severity'] == 'error']


def check(official, plan, contacts='none', profile=None):
    _, model, diagnostics = build_plan(plan, official)
    assert not errors(diagnostics), errors(diagnostics)
    geometry = analyze_geometry(model, official, detail='summary', contacts=contacts)
    assert geometry['complete']
    assert not errors(geometry['diagnostics']), errors(geometry['diagnostics'])
    if profile:  # the vehicle family review: wheel placement and wheel space
        review = review_vehicle(model, official, profile=profile)
        assert not errors(review['diagnostics']), errors(review['diagnostics'])
    return model, geometry


def extremes(spec):
    if 'enum' in spec:
        return list(spec['enum'])
    if spec.get('type') == 'boolean':
        return [False, True]
    if spec.get('type') in ('integer', 'number'):
        return [spec['minimum'], spec['maximum']]
    return []  # colours and arrays keep their defaults in the structured sweep


def corner_cases(generator, full=True):
    """One-at-a-time extremes (full only), all-min, all-max, and a few seeded mixes of the extremes."""
    options = {k: extremes(v) for k, v in generator.params.items()}
    options = {k: v for k, v in options.items() if v}
    cases = [{k: v} for k, vs in options.items() for v in vs] if full else []
    cases.append({k: vs[0] for k, vs in options.items()})
    cases.append({k: vs[-1] for k, vs in options.items()})
    rng = random.Random(generator.id)
    cases += [{k: rng.choice(vs) for k, vs in options.items()} for _ in range(4)]
    if 'length' in options and 'wheelbase' in options:  # the valid corners of the length/wheelbase box
        (low, high), (short, _) = options['length'], options['wheelbase']
        cases += [dict(length=low, wheelbase=short), dict(length=high, wheelbase=short),
                  dict(length=low, wheelbase=low - 8), dict(length=high, wheelbase=high - 8)]
    unique = {json.dumps(c, sort_keys=True): c for c in cases}
    return list(unique.values())


def test_registry_entries_are_uniform_and_valid():
    assert {'house', 'starfighter', 'grand-tourer', 'delivery-van', 'pickup', 'tipper-truck', 'touring-motorcycle',
            'harbour-launch', 'courier-jet', 'arcade-bay', 'street-lantern', 'tree', 'toy-train', 'cargo-ship',
            'stool', 'chair', 'dining-table', 'bed', 'sofa', 'bookshelf'} <= set(IDS)
    for generator in generators.REGISTRY.values():
        entry = generator.entry()
        assert entry['id'] and entry['title'] and entry['families'] and entry['kinds'] and entry['roles']
        jsonschema.Draft202012Validator.check_schema(entry['params'])
        resolved = generator.resolve({})  # defaults are valid
        assert resolved == generator.defaults()
        assert generator.generate()['plan'] == generator.generate()['plan']  # pure: same params, same plan


def test_digest_stays_under_the_token_budget():
    text = generators.digest()
    assert all(i in text for i in IDS)
    assert len(text) / 3 < 1700  # a pessimistic 3 characters per token (1,500 until the furniture designs, #199)
    assert len(text) < 5100


def test_bad_parameters_are_rejected():
    house = generators.get('house')
    for bad in [{'width': 'wide'}, {'roof_style': 'dome'}, {'nope': 1}, {'width': 100}]:
        with pytest.raises(ValueError):
            house.resolve(bad)
    with pytest.raises(ValueError):
        house.generate({'wall_colour': 'plaid'})
    with pytest.raises(ValueError):
        generators.get('nope')


def test_colour_names_resolve_to_real_codes(official):
    entries, colours, _ = catalog_data()
    known = {c['code'] for c in colours.values()}
    assert {colour_code(n) for n in ['red', 'white', 'blue', 'dark grey', 'Dark-Grey', 'dark gray']} == {4, 15, 1, 72}
    assert colour_code('blue', glass=True) == 33 and colour_code('black', glass=True) == 10375 and colour_code('@colours.Red') == 4 and colour_code(7) == 7
    for name, code in NAMES.items():
        assert code in known and code in official.colours_by_code, (name, code)
    with pytest.raises(ValueError):
        colour_code('plaid')


def test_roles_are_tagged_and_accepted_by_the_plan_schema():
    for gid in ['house', 'grand-tourer', 'starfighter', 'arcade-bay']:
        plan = generators.generate(gid)['plan']
        roles = {p['role'] for s in plan['sections'] for step in s['steps'] for p in step if 'role' in p}
        assert roles and roles <= set(generators.get(gid).roles) | {'tyre', 'wheel'}, (gid, roles)
    house = generators.generate('house', dict(roof_colour='blue', chimney='left'))['plan']
    tagged = {p['role']: p['colour'] for s in house['sections'] for step in s['steps'] for p in step if 'role' in p}
    assert tagged['roof'] == 1 and {'wall', 'trim', 'door', 'window', 'chimney'} <= set(tagged)


def test_house_8x6_with_door_and_two_windows_passes_and_has_them(official):
    result = generators.generate('house', dict(width=8, depth=6, windows=2, door_side='left'))
    assert result['notes'] == []
    model, geometry = check(official, result['plan'], contacts='all')
    plan_parts = [p for s in result['plan']['sections'] for step in s['steps'] for p in step]
    frames = [p for p in plan_parts if p['ref'].startswith('window4')]
    doors = [p for p in plan_parts if p['ref'].startswith('door4')]
    assert len(frames) == 2 and len(doors) == 1
    assert geometry['occurrence_count'] > 50


def test_house_reports_what_it_adjusted():
    notes = generators.generate('house', dict(width=7, depth=6, courses=4, windows=6, dormer=True))['notes']
    text = ' '.join(notes)
    assert 'width 7 adjusted to 8' in text and 'courses raised to 6' in text and 'dormer skipped' in text
    assert 'windows fit' in text


@pytest.mark.parametrize('name', ROAD)
def test_palette_from_colour_names_works_for_every_road_design(official, name):
    result = generators.generate(name, dict(body='red', roof='white', trim='blue', wheels='dark grey', glass='trans black'))
    plan = result['plan']
    colours = {p['colour'] for s in plan['sections'] for step in s['steps'] for p in step}
    assert {4, 15, 72} <= colours  # red body, white roof, dark grey rims
    roles = {p.get('role') for s in plan['sections'] for step in s['steps'] for p in step}
    assert {'body', 'roof', 'tyre'} <= roles
    check(official, plan)


@pytest.mark.parametrize('name', [n for n in DESIGNS if n not in ROAD])
def test_palette_from_colour_names_works_for_other_vehicles(official, name):
    plan = generators.generate(name, dict(body='yellow', roof='dark blue'))['plan']
    colours = {p['colour'] for s in plan['sections'] for step in s['steps'] for p in step}
    assert 14 in colours  # the yellow body
    check(official, plan)


@pytest.mark.parametrize('gid', IDS)
def test_defaults_build_without_validation_errors(official, gid):
    check(official, generators.generate(gid)['plan'], contacts='all')


@pytest.mark.parametrize('gid', IDS)
def test_parameter_range_corners_build_without_validation_errors(official, gid):
    generator = generators.get(gid)
    # ponytail: the full sweep took ~10 min (dozens of builds per generator). By default only all-min, all-max,
    # the seeded mixes and the length/wheelbase box; LDRAW_NOVA_FULL_SWEEP=1 adds every one-at-a-time extreme
    # (run it when a generator changes).
    for case in corner_cases(generator, full=os.environ.get('LDRAW_NOVA_FULL_SWEEP') == '1'):
        try:
            result = generator.generate(case)
            check(official, result['plan'], profile=PROFILES.get(gid))
        except AssertionError as exc:
            raise AssertionError(f'{gid} {case}: {exc}') from exc


def test_house_feature_grid_builds_without_validation_errors(official):
    """Window/door/roof combinations that the one-at-a-time sweep cannot reach."""
    house = generators.get('house')
    grid = itertools.product([6, 8, 12, 24], [6, 20], [6, 14], ['left', 'centre', 'right', 'none'],
                             [0, 3, 8], [('front',), ('left', 'right', 'back')], ['gable', 'flat'])
    rng = random.Random(7)
    for width, depth, courses, door, windows, sides, roof in rng.sample(list(grid), 24):
        params = dict(width=width, depth=depth, courses=courses, door_side=door, windows=windows,
                      window_sides=list(sides), roof_style=roof, porch=rng.random() < .5,
                      chimney=rng.choice(['none', 'left', 'right']), dormer=rng.random() < .5)
        try:
            check(official, house.generate(params)['plan'])
        except AssertionError as exc:
            raise AssertionError(f'{params}: {exc}') from exc


def test_cli_generate_lists_digests_and_writes_a_plan(tmp_path, capsys):
    def run(*argv):
        args = cli.parser().parse_args(['generate', *argv])
        return cli.run(args)
    listed, status = run('list', '--family', 'house')
    ids = [row['id'] for row in listed]
    assert status == 0 and ids[0] == 'building' and 'house' in ids      # the grammar answers house requests; the old generator stays
    assert run('list', '--kind', 'pickup truck')[0][0]['id'] == 'pickup'
    digest, _ = run('digest')
    assert digest['approx_tokens'] < 1700
    assert run('colours', 'dark grey', 'red')[0] == {'dark grey': 72, 'red': 4}
    target = tmp_path / 'h.json'
    report, status = run('plan', 'house', '--param', 'width=10', '--param', 'door_side=right', '--no-check', '--output', str(target))
    assert status == 0 and report['params']['width'] == 10 and json.loads(target.read_text())['sections']
    with pytest.raises(ValueError):
        run('plan', 'house', '--param', 'width=oops', '--no-check')


def test_cli_generate_plan_checks_the_build(official, tmp_path):
    args = cli.parser().parse_args(['generate', 'plan', 'house', '--output', str(tmp_path / 'h.json')])
    report, status = cli.run(args)
    assert status == 0 and report['checks_passed'] and (tmp_path / 'h.json').is_file()


def test_tree_defaults_match_the_atlas_tree_and_the_trunk_grows_by_courses():
    from ldraw_tools.architecture import tree
    flat = lambda plan: [{k: v for k, v in p.items() if k not in ('role', 'id')} for s in plan['sections'] for st in s['steps'] for p in st]
    default = generators.generate('tree', dict(foliage_colour=288, foliage_accent=2, trunk_colour=70, flower_colour=5, ground_colour=19))
    assert flat(default['plan']) == flat(tree().plan())
    short, tall = (generators.generate('tree', dict(trunk_courses=n))['plan'] for n in (3, 10))
    height = lambda plan: -min(p['at'][1] for s in plan['sections'] for st in s['steps'] for p in st)
    assert height(tall) - height(short) == 7 * 24
    roles = {p['role'] for s in tall['sections'] for st in s['steps'] for p in st}
    assert roles == {'base', 'trunk', 'foliage', 'flower'}
    assert generators.find(kind='oak tree')[0].id == 'tree'


def test_train_grows_by_wagons_and_ship_clamps_its_bays(official):
    parts = lambda plan: [p for s in plan['sections'] for st in s['steps'] for p in st]
    depth = lambda plan: max(p['at'][2] for p in parts(plan)) // 20 + 1
    short, long = (generators.generate('toy-train', dict(wagons=n))['plan'] for n in (0, 4))
    assert depth(long) - depth(short) == 4 * 10
    assert {p['role'] for p in parts(long)} == set(generators.get('toy-train').roles)
    assert generators.find(kind='steam train')[0].id == 'toy-train' and generators.find(kind='container ship')[0].id == 'cargo-ship'
    result = generators.generate('cargo-ship', dict(length=16, bays=7))
    assert result['params']['bays'] == 7 and 'bays adjusted to 1' in ' '.join(result['notes'])
    assert generators.generate('cargo-ship', dict(length=17, bays=0))['notes'] == ['length adjusted to 18 (even)']
    for plan in (short, long, result['plan']):               # one connected build: nothing floats
        model = check(official, plan, contacts='all')[1]
        assert model['optimistic_component_count'] == 1
