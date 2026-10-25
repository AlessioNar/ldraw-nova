"""ontology/shapes.ttl holds the validity rules of the grammar as SHACL (issue #93): every preset conforms, each rule catches its case."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip('rdflib')
pytest.importorskip('pyshacl')
ROOT = Path(__file__).resolve().parent.parent

from ldraw_tools.grammar.presets import PRESETS  # noqa: E402
from ldraw_tools.grammar.schema import CONSTRAINTS  # noqa: E402

# Checked at build time by the generators, which know the facade length; not parameters, so not shapes.
BUILD_TIME = {'door-fits', 'window-height'}

# rule id -> a config (over the defaults) that breaks that rule (and perhaps an overlapping one)
BREAKING = {
    'door-height': {'storeys': {'courses': 4}},
    'pitched-needs-rect': {'footprint': {'shape': 'L', 'width': 16, 'depth': 12}},
    'towers-flat': {'corners': {'style': 'towers'}, 'roof': {'style': 'hip'}},
    'setback-rect': {'footprint': {'shape': 'L', 'width': 16, 'depth': 12}, 'storeys': {'setback': 1}, 'roof': {'style': 'flat'}},
    'thick-walls': {'footprint': {'width': 5, 'depth': 6}, 'walls': {'thickness': 2}},
    'notch-fits': {'footprint': {'shape': 'L', 'width': 10, 'depth': 12, 'notch_width': 8}, 'roof': {'style': 'flat'}},
    'chimney-pitched': {'roof': {'style': 'flat'}, 'details': {'chimney': 'left'}},
    'porch-door': {'details': {'porch': True}, 'openings': {'door_facade': 'back'}},
    'balcony-storeys': {'details': {'balcony': True}, 'storeys': {'count': 1}},
    'parapet-flat': {'roof': {'style': 'hip', 'parapet': 'plain'}},
    'modular-fits': {'base': {'kind': 'modular'}},
    'interior-rect': {'footprint': {'shape': 'L', 'width': 16, 'depth': 12}, 'storeys': {'interior': 'partition'}, 'roof': {'style': 'flat'}},
    'eaves-setback': {'storeys': {'eaves': True}},
    'setback-fits': {'footprint': {'width': 8, 'depth': 8}, 'storeys': {'count': 4, 'setback': 1}},
    'eaves-rect': {'footprint': {'shape': 'L', 'width': 16, 'depth': 12}, 'storeys': {'eaves': True, 'band_projection': 1, 'setback': 1}, 'roof': {'style': 'flat'}},
    'fence-base': {'details': {'fence': True}, 'base': {'margin': 1}},
    'flowers-sills': {'details': {'flower_boxes': True}},
    'gable-window-fits': {'roof': {'style': 'hip', 'gable_windows': True}},
    'sign-shop': {'details': {'sign': 'fascia'}, 'storeys': {'courses': 6}},
}


@pytest.fixture(scope='module')
def shapes():
    spec = importlib.util.spec_from_file_location('validate_shapes', ROOT / 'tools' / 'validate_shapes.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_rule_has_a_shape_or_is_build_time():
    from rdflib import Graph, Namespace
    g = Graph().parse(ROOT / 'ontology' / 'shapes.ttl', format='turtle')
    ids = {str(o) for o in g.objects(None, Namespace('http://purl.org/dc/terms/').identifier)}
    assert ids == {rule for rule, _ in CONSTRAINTS} - BUILD_TIME
    assert set(BREAKING) == ids


@pytest.mark.parametrize('name', list(PRESETS))
def test_presets_conform(shapes, name):
    assert shapes.check(PRESETS[name]['config']) == []


@pytest.mark.parametrize('rule', list(BREAKING))
def test_a_breaking_config_names_its_rule(shapes, rule):
    assert rule in {r for r, _ in shapes.check(BREAKING[rule], repair=False)}  # some rules overlap (eaves-rect also trips setback-rect)


@pytest.mark.parametrize('rule', list(BREAKING))
def test_normalise_repairs_what_the_shapes_catch(shapes, rule):
    from ldraw_tools.grammar.schema import normalise
    try:
        normalise(BREAKING[rule])
    except ValueError:
        return  # rejected outright, also a repair of sorts
    assert shapes.check(BREAKING[rule]) == []


def test_a_building_needs_one_of_each_component(shapes):
    g = shapes.config_graph({})  # a building with no components and no name
    conforms, *_ = shapes.validate(g, shacl_graph=shapes._graph('shapes'), inference='none')
    assert not conforms
