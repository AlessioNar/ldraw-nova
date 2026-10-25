"""ontology/sets.ttl and sets-shapes.ttl (issue #118): brands are data, and LEGO and CADA sets pass the same shape."""
from pathlib import Path

import pytest

pytest.importorskip('rdflib')
pytest.importorskip('pyshacl')
from pyshacl import validate  # noqa: E402
from rdflib import BNode, Graph, Literal, Namespace, RDF  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OBD = Namespace('https://alessionar.github.io/open-brick-designer/ns/obd#')
BRAND = Namespace('https://alessionar.github.io/open-brick-designer/ns/brand/')
EX = Namespace('https://example.org/sets/')
PFX = '''PREFIX obd: <https://alessionar.github.io/open-brick-designer/ns/obd#>
PREFIX brand: <https://alessionar.github.io/open-brick-designer/ns/brand/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
'''


@pytest.fixture(scope='module')
def vocab():
    return Graph().parse(ROOT / 'ontology' / 'sets.ttl', format='turtle')


@pytest.fixture(scope='module')
def shapes():
    return Graph().parse(ROOT / 'ontology' / 'sets-shapes.ttl', format='turtle')


@pytest.fixture()
def sample(vocab):
    g = Graph().parse(ROOT / 'tests' / 'fixtures' / 'sets-sample.ttl', format='turtle')
    return g + vocab


def conforms(g, shapes):
    ok, _, report = validate(g, shacl_graph=shapes, inference='none')
    return ok, report


def test_sample_lego_and_cada_sets_conform(sample, shapes):
    ok, report = conforms(sample, shapes)
    assert ok, report


def test_brands_are_a_scheme_with_alt_labels(vocab):
    rows = vocab.query(PFX + 'SELECT ?b ?alt WHERE { ?b skos:inScheme brand: ; skos:altLabel ?alt }')
    labels = {(str(b).rsplit('/', 1)[1], str(a)) for b, a in rows}
    assert ('lego', 'Lego') in labels and ('cada', 'CaDA') in labels


def test_both_brands_are_found_by_one_query(sample):
    rows = sample.query(PFX + 'SELECT ?lbl WHERE { ?s a obd:Set ; obd:brand/skos:prefLabel ?lbl }')
    assert sorted(str(r[0]) for r in rows) == ['CADA', 'LEGO']


def test_a_new_brand_is_data_only(sample, shapes):
    sample.add((BRAND.mouldking, RDF.type, Namespace('http://www.w3.org/2004/02/skos/core#').Concept))
    sample.set((EX['lego-10183-1'], OBD.brand, BRAND.mouldking))
    assert conforms(sample, shapes)[0]


def test_set_without_brand_or_name_fails(sample, shapes):
    sample.remove((EX['lego-10183-1'], OBD.brand, None))
    sample.remove((EX['cada-c61005w'], Namespace('http://purl.org/dc/terms/').title, None))
    ok, report = conforms(sample, shapes)
    assert not ok and 'one brand' in report and 'has a name' in report


@pytest.mark.parametrize('qty', [0, -3])
def test_non_positive_quantity_fails(sample, shapes, qty):
    line = BNode()
    for p, o in ((RDF.type, OBD.SetPart), (OBD.part, Literal('3001')), (OBD.colour, Literal(4)), (OBD.quantity, Literal(qty))):
        sample.add((line, p, o))
    sample.add((EX['cada-c61005w'], OBD.contains, line))
    ok, report = conforms(sample, shapes)
    assert not ok and 'positive quantity' in report
