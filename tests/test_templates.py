"""Templates from builds: derived params, pure generate(), validity of every offered variant (docs/plans/build-templates)."""

import json

import jsonschema
import pytest

from ldraw_tools import editor, generators, role_edits, roles, templates
from ldraw_tools.builder import build_plan, check_schema
from ldraw_tools.geometry import analyze_geometry
from ldraw_tools.templates import derive

IDS = ['small-house', 'flatbed-truck', 'willowbank-cottage', 'passenger-car']


def parts_of(plan):
    return [p for s in plan['sections'] for step in s['steps'] for p in step]


def check(official, plan):
    _, model, diagnostics = build_plan(plan, official)
    errors = [d['message'] for d in diagnostics if d['severity'] == 'error']
    geometry = analyze_geometry(model, official, detail='summary', contacts='all')
    errors += [d['message'] for d in geometry['diagnostics'] if d['severity'] == 'error']
    return errors, geometry['optimistic_component_count']


def test_prototypes_ship_and_register():
    assert set(IDS) <= set(templates.REGISTRY)
    for tid in IDS:
        t = templates.get(tid)
        entry = t.entry()
        assert entry['kind'] == 'template' and entry['families'] and entry['kinds'] and entry['roles']
        jsonschema.Draft202012Validator.check_schema(entry['params'])
        assert any(k.startswith('colour_') for k in entry['params'])      # colours by role: always
        assert t.resolve({}) == t.defaults()
        assert entry['source'].get('author') or entry['source'].get('name')
    assert templates.find('house', 'cottage')[0].id == 'willowbank-cottage'
    assert templates.find(None, 'flatbed truck')[0].id == 'flatbed-truck'
    text = templates.digest()
    assert all(i in text for i in IDS) and 'template' in text and len(text) < 4000


def test_defaults_reproduce_the_source_exactly():
    for tid in IDS:
        t = templates.get(tid)
        result = t.generate()
        plan = result['plan']
        check_schema(plan)
        assert len(parts_of(plan)) == len(t.data['leaves'])
        assert result['plan'] == t.generate()['plan']                        # pure
        source = {l['id']: l for l in t.data['leaves']}
        for p in parts_of(plan):
            l = source[p['id']]
            assert p['colour'] == l['colour'] and p['at'] == [round(v, 6) + 0.0 for v in l['at']] and p['role'] == l['role']


def test_colour_by_role_changes_only_that_role():
    for tid in IDS:
        t = templates.get(tid)
        base = {p['id']: p for p in parts_of(t.generate()['plan'])}
        role = next(r for r in t.roles if r not in ('glass', 'window'))
        value = 'bright green' if t.data['colours'][role] != 10 else 'blue'
        after = {p['id']: p for p in parts_of(t.generate({f'colour_{role}': value})['plan'])}
        assert set(base) == set(after)
        for i, p in base.items():
            assert (after[i]['colour'] == (10 if value == 'bright green' else 1)) == (p['role'] == role) or p['role'] == role
            if p['role'] != role:
                assert after[i]['colour'] == p['colour']


def test_bad_parameters_are_rejected():
    t = templates.get('flatbed-truck')
    for bad in [{'z_delta': 99}, {'z_delta': -99}, {'colour_body': 'plaid'}, {'nope': 1}, {'swap_tile_1x2': '999'}]:
        with pytest.raises(ValueError):
            t.generate(bad)


def test_every_knob_changes_the_plan_in_the_way_it_says():
    for tid in IDS:
        t = templates.get(tid)
        base = parts_of(t.generate()['plan'])
        for name, spec in t.params.items():
            if name.startswith('include_'):
                fewer = parts_of(t.generate({name: False})['plan'])
                assert len(fewer) == len(base) - len(t.data['groups'][name.removeprefix('include_')]['leaves'])
            elif name == 'courses_delta':
                taller = parts_of(t.generate({name: max(spec['enum'])})['plan'])
                assert len(taller) > len(base)
                shorter = parts_of(t.generate({name: min(spec['enum'])})['plan'])
                assert len(shorter) < len(base)
            elif name.endswith('_delta'):
                longer = t.generate({name: max(spec['enum'])})['plan']
                # the same parts, longer ones and moved ones; a part with no longer sibling gets a filler after it
                assert parts_of(longer) != base and len(base) <= len(parts_of(longer)) <= 1.3 * len(base)
            elif name.endswith('_repeat'):
                assert len(parts_of(t.generate({name: max(spec['enum'])})['plan'])) > len(base)
                if min(spec['enum']) < 0:
                    assert len(parts_of(t.generate({name: min(spec['enum'])})['plan'])) < len(base)
            elif name.startswith('swap_'):
                chosen = next(c for c in spec['enum'] if c != 'keep')
                swapped = parts_of(t.generate({name: chosen})['plan'])
                assert sum(p['ref'] == chosen + '.dat' for p in swapped) >= 1


def test_role_edits_work_on_template_output():
    plan = templates.generate('willowbank-cottage')['plan']
    ops = role_edits.recolour_role(plan, 'roof', 'blue')
    edited = editor.apply(plan, ops)
    assert {p['colour'] for p in parts_of(edited) if p['role'] == 'roof'} == {1}


def test_flatbed_truck_variants_are_valid_and_not_less_connected(official):
    t = templates.get('flatbed-truck')
    source_errors, source_components = check(official, t.generate()['plan'])
    spec = t.params['z_delta']
    for value in (min(spec['enum']), max(spec['enum'])):
        errors, components = check(official, t.generate({'z_delta': value})['plan'])
        assert len(errors) <= len(source_errors) and components <= source_components, value
    errors, components = check(official, t.generate({'colour_body': 'blue', 'swap_tile_1x2': 'keep'})['plan'])
    assert not errors


def test_small_house_courses_and_removals_are_valid(official):
    t = templates.get('small-house')
    source_errors, source_components = check(official, t.generate()['plan'])
    for value in (min(t.params['courses_delta']['enum']), max(t.params['courses_delta']['enum'])):
        errors, components = check(official, t.generate({'courses_delta': value})['plan'])
        assert len(errors) <= len(source_errors) and components <= source_components, value


def test_derive_from_a_generator_plan_finds_knobs_without_hand_written_code(official):
    plan = generators.generate('house', dict(width=12, depth=8, courses=8, windows=2, chimney='left'))['plan']
    data, report = derive.derive(plan, official, id='gen-house', title='Generated house', families=['house'], kinds=['house'],
                                 description='A house', verify=False)
    assert report['leaves'] > 100 and data['stretch'] and data['courses']
    assert {'wall', 'roof', 'window'} <= set(data['roles']) and any(k.startswith('include_') for k in data['params'])
    t = templates.from_data(derive.compact(data))
    variant = t.generate({'colour_roof': 'blue'})['plan']
    check_schema(variant)
    assert roles.role_of(variant)


def test_stretch_option_sets_come_from_equivalent_library_parts(official):
    facts = roles.PartFacts(official)
    fam = derive.Families(official, facts)
    assert derive.family_key('Brick  2 x  4') == 'Brick N x M' and derive.family_key('Brick 1 x 2 x 5') is None
    assert derive.candidate_for(fam, facts, '3004', 0, 2)[0] == '3010'          # 1x2 brick + 2 studs = 1x4 brick
    assert derive.candidate_for(fam, facts, '3010', 0, -2)[0] == '3004'
    assert derive.candidate_for(fam, facts, '3004', 0, 5) is None              # no 1x7 brick exists
    assert derive.candidate_for(fam, facts, '3069b', 0, 2)[0] == '2431'          # 1x2 tile with groove + 2 studs


def test_candidates_route_between_generators_and_templates():
    rows = templates.candidates('house', 'cottage')
    assert rows[0]['source'] == 'generator' and rows[0]['id'] == 'house'                    # a tie goes to the generator
    assert any(r['source'] == 'template' and r['id'] == 'willowbank-cottage' for r in rows)
    top = templates.candidates('truck', 'flatbed truck')[0]
    assert (top['source'], top['id']) == ('template', 'flatbed-truck')                      # the exact kind only a template has
    assert templates.candidates('garden', 'zzz')[0]['source'] == 'generator'               # family-only match still routes


def test_the_build_worker_and_the_registry_dump_know_templates(official, tmp_path):
    """The fast path sees templates as registry entries 'template:<id>' and the worker builds them by that id."""
    import ast
    import subprocess
    import sys
    from pathlib import Path

    from ldraw_tools import pipeline
    root = Path(__file__).resolve().parents[1]
    source = (root / 'app' / 'web' / 'backend' / 'generator_bridge.py').read_text(encoding='utf-8')
    dump = next(ast.literal_eval(node.value) for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == '_DUMP')
    out = subprocess.run([sys.executable, '-c', dump], cwd=root, capture_output=True, text=True, timeout=120, check=True)
    registry = json.loads(out.stdout)
    ids = {e['id'] for e in registry['entries']}
    assert {'house', 'template:flatbed-truck', 'template:willowbank-cottage'} <= ids and 'template' in registry['digest']
    result = pipeline.Pipeline().run(tmp_path, generator='template:flatbed-truck', params={'z_delta': 2}, name='truck',
                                     geometry=False, glb=False)
    assert result['ok'] and result['generator']['generator'] == 'flatbed-truck' and result['generator']['params']['z_delta'] == 2


def test_a_part_with_no_longer_sibling_gets_a_filler_of_its_family(official):
    """A 1 x 3 brick has no 1 x 5: lengthening it by 2 keeps it and closes the gap with a 1 x 2 after it."""
    from ldraw_tools.templates import core
    facts = roles.PartFacts(official)
    fam = derive.Families(official, facts)
    for sign in (1, -1):                      # the part's local x runs with or against world x
        matrix = [[sign, 0, 0], [0, 1, 0], [0, 0, sign]]
        leaf = dict(id='l0001', ref='3622.dat', colour=4, role='wall', at=[100.0, 0.0, 0.0], matrix=matrix)
        options = derive._extensions(leaf, 0, 0, fam, facts, [2])
        code, move, turned, filler, offset, filler_turned = options[2]
        assert (code, move, filler) == ('3622', 0.0, '3004')
        twin = core._filler(dict(leaf), (filler, offset, filler_turned), 0)
        brick, gap = facts.get('3622.dat')['bounds'], facts.get('3004.dat')['bounds']
        ends = sorted([100 + sign * brick[0][0], 100 + sign * brick[1][0]])
        twin_ends = sorted([twin['at'][0] + sign * gap[0][0], twin['at'][0] + sign * gap[1][0]])
        assert abs(twin_ends[0] - ends[1]) < 1e-6 and abs(twin_ends[1] - ends[1] - 40) < 1e-6
