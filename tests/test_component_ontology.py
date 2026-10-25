"""ontology/obd.ttl is the source of the component tree and parameters; the hand-kept schema must agree until #94 generates it."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip('rdflib')
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope='module')
def gen():
    spec = importlib.util.spec_from_file_location('build_obd', ROOT / 'tools' / 'build_obd.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_schema_is_in_sync_with_the_ontology(gen):
    assert gen.diff(*gen.load()) == []


def test_component_tree(gen):
    groups, building = gen.load()
    assert [c['component'] for c in groups.values()] == [
        'Footprint', 'Storeys', 'Walls', 'Openings', 'Corners', 'Roof', 'Details', 'Base']
    assert list(building) == ['name']


def test_stamped_roles_exist_and_are_grammar_roles(gen):
    from ldraw_tools.grammar.roles import ROLE_NAMES
    groups, _ = gen.load()
    stamped = {r for c in groups.values() for r in c['roles']}
    assert stamped and stamped <= set(ROLE_NAMES)


def test_diff_catches_a_changed_default(gen, tmp_path):
    text = (ROOT / 'ontology' / 'obd.ttl').read_text(encoding='utf-8')
    changed = text.replace('obd:default 12 ;', 'obd:default 13 ;', 1)
    assert changed != text
    path = tmp_path / 'obd.ttl'
    path.write_text(changed, encoding='utf-8')
    assert any('footprint.width' in line for line in gen.diff(*gen.load(path)))


@pytest.fixture(scope='module')
def export():
    spec = importlib.util.spec_from_file_location('export_schema', ROOT / 'docs' / 'plans' / 'build-grammar' / 'export_schema.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_schema_equals_the_hand_kept_one(export):
    from ldraw_tools.grammar.schema import SCHEMA
    assert export.build_schema() == SCHEMA


def test_committed_schema_and_params_are_current(export):
    assert export.main(['--check']) == 0
