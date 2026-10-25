"""ontology/roles.ttl is the source of the role vocabulary; role_vocab.py must be exactly what it generates."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip('rdflib')
SCRIPT = Path(__file__).resolve().parent.parent / 'tools' / 'build_role_vocab.py'


@pytest.fixture(scope='module')
def gen():
    spec = importlib.util.spec_from_file_location('build_role_vocab', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_role_vocab_is_in_sync_with_the_skos_scheme(gen):
    assert gen.main(['--check']) == 0


def test_scheme_content(gen):
    roles, grammar_roles, alias, words, _, subroles = gen.load()
    assert alias['tower'] == 'wall' and alias['hull'] == 'body'
    assert set(grammar_roles) <= set(roles) and set(alias.values()) <= set(roles)
    assert set(words.values()) <= set(roles)
    assert subroles['wall:stripe-a'] == 'wall' and 'wall:stripe-a' not in alias
    assert set(subroles.values()) <= set(roles) and not set(subroles) & set(roles)
