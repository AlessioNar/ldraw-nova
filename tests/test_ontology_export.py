"""docs/ns/ is what tools/export_ontology.py generates from ontology/*.ttl, and the SPARQL cookbook runs against it."""
import importlib.util
import re
from pathlib import Path

import pytest

pytest.importorskip('rdflib')
from rdflib import Graph  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COOKBOOK = ROOT / 'docs' / 'ontology' / 'SPARQL.md'


@pytest.fixture(scope='module')
def export():
    spec = importlib.util.spec_from_file_location('export_ontology', ROOT / 'tools' / 'export_ontology.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_published_files_are_in_sync(export):
    assert export.main(['--check']) == 0


def test_export_is_idempotent(export):
    assert export.render() == export.render()


def test_merged_file_holds_every_source_graph(export):
    sizes = [len(Graph().parse(export.SRC / n)) for n in export.FILES]
    assert all(len(Graph().parse(export.OUT / n)) == size for n, size in zip(export.FILES, sizes))
    assert len(Graph().parse(export.OUT / export.MERGED)) == sum(sizes)


def _queries():
    return re.findall(r'```sparql\n# cookbook: (\S+)\n(.*?)```', COOKBOOK.read_text(encoding='utf-8'), re.S)


def test_cookbook_has_queries():
    assert len(_queries()) >= 7


@pytest.mark.parametrize('name,query', _queries(), ids=[n for n, _ in _queries()])
def test_cookbook_query_returns_rows(name, query):
    g = Graph().parse(ROOT / 'docs' / 'ns' / 'ontology.ttl')
    assert len(list(g.query(query))) > 0, name
