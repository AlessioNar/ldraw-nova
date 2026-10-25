"""The grammar's plans through the toolkit's own build and geometry validation (needs the LDraw library)."""
import json

import pytest

from ldraw_tools import generators, grammar
from ldraw_tools.builder import build_plan
from ldraw_tools.geometry import analyze_geometry
from ldraw_tools.grammar.presets import PRESETS


def errors(diagnostics):
    return [d for d in diagnostics if d['severity'] == 'error']


def validate(official, plan):
    _, model, diagnostics = build_plan(plan, official)
    assert not errors(diagnostics), errors(diagnostics)
    geometry = analyze_geometry(model, official, detail='summary', contacts='all')
    assert geometry['complete']
    assert not errors(geometry['diagnostics']), errors(geometry['diagnostics'])
    assert geometry['optimistic_component_count'] == 1, geometry['optimistic_component_count']
    return geometry


@pytest.mark.parametrize('name', list(PRESETS))
def test_presets_validate_with_one_connected_component(official, name):
    result = grammar.generate(preset=name)
    validate(official, result['plan'])


SWEEPS = [
    {'footprint': {'width': 8, 'depth': 6}},
    {'footprint': {'width': 22, 'depth': 12}, 'storeys': {'count': 3}},
    {'roof': {'style': 'hip'}, 'footprint': {'width': 14, 'depth': 12}, 'storeys': {'count': 3, 'setback': 1}},
    {'roof': {'style': 'gable', 'ridge': 'z'}, 'details': {'chimney': 'centre'}},
    {'roof': {'style': 'flat', 'parapet': 'battlement'}, 'footprint': {'shape': 'L', 'width': 16, 'depth': 12}},
    {'footprint': {'shape': 'U', 'width': 20, 'depth': 12}, 'walls': {'thickness': 2}, 'roof': {'style': 'flat'}},
    {'footprint': {'shape': 'courtyard', 'width': 16, 'depth': 16, 'court_width': 6, 'court_depth': 6}, 'roof': {'style': 'flat'}},
    {'corners': {'style': 'towers', 'tower_shape': 'round', 'tower_roof': 'cone'}},
    {'corners': {'style': 'towers', 'tower_shape': 'square', 'tower_roof': 'cone', 'tower_size': 5}},
    {'openings': {'ground_windows': 'wide', 'upper_windows': 'arch'}, 'walls': {'style': 'stone', 'thickness': 2}},
    {'openings': {'ground_windows': 'rect', 'upper_windows': 'rect', 'door': 'glazed'}, 'footprint': {'width': 20, 'depth': 10},
     'corners': {'style': 'pillars'}},
    {'details': {'porch': True, 'sign': 'fascia'}, 'walls': {'style': 'timber'}},
    {'details': {'balcony': True}, 'storeys': {'band_projection': 1}},
]


@pytest.mark.parametrize('config', SWEEPS, ids=lambda c: json.dumps(c, sort_keys=True)[:70])
def test_parameter_sweeps_validate(official, config):
    validate(official, grammar.generate(config)['plan'])


def test_parts_aware_plan_validates(official):
    rows = [dict(part='3004', colour='tan', qty=400), dict(part='3010', colour='tan', qty=200), dict(part='3005', colour='tan', qty=80)]
    result = grammar.generate(preset='house', inventory=rows, mode='only')
    validate(official, result['plan'])


def test_registry_entry_builds_through_the_generator_interface(official):
    validate(official, generators.generate('building', {'roof': {'style': 'hip'}})['plan'])


def test_validate_helper_agrees(official):
    from ldraw_tools.grammar.validate import check
    verdict = check(grammar.generate(preset='cottage')['plan'], official)
    assert verdict['ok'] and verdict['components'] == 1


def test_catalogue_boxes_match_the_library(official):
    """Each part's local footprint and height in catalog.py agree with the part's real bounds (infill parts excluded)."""
    from ldraw_tools.grammar import catalog
    infill = {catalog.PANE_4X3, catalog.PANE_ARCH, catalog.SHOP_PANE, *catalog.DOOR_LEAF.values()}
    wrong = []
    for code, spec in catalog.PARTS.items():
        if code in infill:
            continue
        plan = dict(version=1, author='t', sections=[dict(name='t.ldr', description='t', steps=[[dict(id='a', ref=code + '.dat', colour=4, at=[0, 0, 0])]])])
        _, model, _ = build_plan(plan, official)
        bounds = analyze_geometry(model, official, detail='summary', contacts='none')['bounds']
        lo, hi = bounds['min'], bounds['max']
        x0, x1, z0, z1 = spec.box
        got = (lo[0] / 20, hi[0] / 20, lo[2] / 20, hi[2] / 20, hi[1] / 8)
        want = (x0, x1, z0, z1, spec.h)
        if any(abs(a - b) > 0.06 for a, b in zip(got, want)):
            wrong.append((code, got, want))
    assert not wrong, wrong


def test_exported_schema_is_current():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'docs/plans/active/build-grammar/ontology.schema.json'
    assert json.loads(path.read_text(encoding='utf-8')) == json.loads(json.dumps(grammar.SCHEMA))
