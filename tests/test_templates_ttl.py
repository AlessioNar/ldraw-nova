"""ontology/templates.ttl is the RDF catalogue of the reference builds (issue #95): in sync with the JSON, valid, and queryable."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip('rdflib')
pytest.importorskip('pyshacl')
from pyshacl import validate  # noqa: E402
from rdflib import Graph  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ONT = ROOT / 'ontology'
PFX = '''PREFIX obd: <https://alessionar.github.io/open-brick-designer/ns/obd#>
PREFIX role: <https://alessionar.github.io/open-brick-designer/ns/role/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>
'''


@pytest.fixture(scope='module')
def gen():
    spec = importlib.util.spec_from_file_location('build_templates_ttl', ROOT / 'tools' / 'build_templates_ttl.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def graph():
    g = Graph()
    for name in ('templates', 'roles', 'obd'):
        g.parse(ONT / f'{name}.ttl', format='turtle')
    return g


def test_catalogue_is_in_sync_with_the_json(gen):
    assert gen.TTL.read_text(encoding='utf-8') == gen.render()


def test_catalogue_conforms_to_its_shapes(graph):
    shapes = Graph().parse(ONT / 'templates-shapes.ttl', format='turtle')
    ok, _, report = validate(graph, shacl_graph=shapes, inference='none')
    assert ok, report


def test_every_template_is_listed(graph):
    ids = {str(r[0]) for r in graph.query(PFX + 'SELECT ?i WHERE { ?t a obd:Template ; dcterms:identifier ?i }')}
    assert ids == {p.stem for p in (ROOT / 'ldraw_tools' / 'data' / 'templates').glob('*.json')}


def test_roles_and_knobs_match_the_json(graph):
    for p in (ROOT / 'ldraw_tools' / 'data' / 'templates').glob('*.json'):
        data = json.loads(p.read_text(encoding='utf-8'))
        rows = graph.query(PFX + f'SELECT ?r WHERE {{ <https://alessionar.github.io/open-brick-designer/ns/template/{data["id"]}> obd:carriesRole ?r }}')
        assert {str(r[0]).rsplit('/', 1)[1] for r in rows} == set(data['roles'])
        rows = graph.query(PFX + f'SELECT ?k WHERE {{ <https://alessionar.github.io/open-brick-designer/ns/template/{data["id"]}> obd:hasKnob/dcterms:identifier ?k }}')
        assert {str(r[0]) for r in rows} == set(data['params'])


def test_query_templates_by_role_and_licence(graph):
    rows = graph.query(PFX + 'SELECT ?i WHERE { ?t obd:carriesRole role:wheel ; dcterms:identifier ?i }')
    assert {str(r[0]) for r in rows} == {'flatbed-truck', 'passenger-car', 'tractor-trailer'}
    rows = graph.query(PFX + '''SELECT ?a WHERE { ?t dcterms:identifier "small-house" ; prov:wasAttributedTo ?x . ?x <http://www.w3.org/2000/01/rdf-schema#label> ?a }''')
    assert {str(r[0]) for r in rows} == {'Marc Giraudet [Mad_Marc]'}


def test_shape_rejects_a_template_without_provenance(graph):
    g = Graph()
    g.parse(data=PFX.replace('PREFIX ', '@prefix ').replace('>\n', '> .\n') + '''
<urn:t> a obd:Template ; dcterms:title "x" ; dcterms:identifier "x" .''', format='turtle')
    for name in ('roles', 'obd'):
        g.parse(ONT / f'{name}.ttl', format='turtle')
    shapes = Graph().parse(ONT / 'templates-shapes.ttl', format='turtle')
    ok, _, _ = validate(g, shacl_graph=shapes, inference='none')
    assert not ok
