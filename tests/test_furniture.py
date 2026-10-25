"""The furniture catalogue harvested from the reference sets, the generators drawn from it and ontology/furniture.ttl (issue #199)."""
import itertools
import json
import re
from pathlib import Path

import pytest

from ldraw_tools import furniture, generators
from test_generators import check

ROOT = Path(__file__).resolve().parent.parent
IDS = list(furniture.GENERATOR_KINDS)


def parts_of(plan):
    return [p for s in plan['sections'] for st in s['steps'] for p in st]


def test_the_catalogue_covers_every_kind_the_generators_draw_from():
    catalogue = furniture.catalogue()
    assert {k for kinds in furniture.GENERATOR_KINDS.values() for k in kinds} <= set(catalogue)
    for kind, row in catalogue.items():
        assert row['pieces'] >= row['sets'] >= 1 and len(row['footprint_studs']) == 2 and row['parts'], kind
        assert [p['pieces'] for p in row['parts']] == sorted((p['pieces'] for p in row['parts']), reverse=True)
    assert set(furniture.GENERATOR_KINDS) <= set(generators.REGISTRY)


def test_the_harvest_is_reproducible_from_the_reference_sets(reference_models, tmp_path):
    import importlib.util
    if not (ROOT / 'tools' / 'harvest_furniture.py').is_file() or not (ROOT / 'data' / 'categories').is_dir():
        pytest.skip('the harvest script is not in this checkout (the image ships the toolkit only)')
    spec = importlib.util.spec_from_file_location('harvest_furniture', ROOT / 'tools' / 'harvest_furniture.py')
    harvest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harvest)
    assert harvest.harvest(Path(reference_models)) == furniture.catalogue()


def test_each_generator_follows_the_parts_its_sets_use():
    for gid in IDS:
        used = {re.sub(r'\.dat$', '', p['ref']) for p in parts_of(generators.generate(gid)['plan'])}
        share = len(used & furniture.vocabulary(gid)) / len(used)
        assert share >= .3, (gid, sorted(used - furniture.vocabulary(gid)))


@pytest.mark.parametrize('gid', IDS)
def test_footprint_is_what_the_plan_occupies(gid, official):
    g = generators.get(gid)
    keys = [k for k, v in g.params.items() if v.get('type') == 'integer']
    cases = [{}, *(dict(zip(keys, c)) for c in itertools.product(*[(g.params[k]['minimum'], g.params[k]['maximum']) for k in keys]))]
    for params in cases:
        width, depth, height = furniture.footprint(gid, **params)
        plan = generators.generate(gid, params)['plan']
        placed = parts_of(plan)
        assert height == max(-p['at'][1] for p in placed), (gid, params)
        xs = [p['at'][0] / 20 for p in placed]
        assert max(xs) - min(xs) <= width and max(abs(x) for x in xs) <= width / 2, (gid, params)
        assert max(abs(p['at'][2] / 20) for p in placed) <= depth / 2, (gid, params)
        model = check(official, plan, contacts='all')[1]                    # nothing floats, whatever the parameters
        assert model['optimistic_component_count'] == 1, (gid, params)


def test_the_generators_route_by_name():
    for text, gid in {'sofa': 'sofa', 'couch': 'sofa', 'armchair': 'chair', 'desk': 'dining-table', 'bookcase': 'bookshelf', 'bar stool': 'stool',
                      'double bed': 'bed'}.items():
        assert generators.find(kind=text)[0].id == gid, text
    assert generators.find(family='furniture')


def test_a_piece_is_built_through_its_generator():
    result = furniture.piece('bed', width=6)
    assert result['generator'] == 'bed' and result['params']['width'] == 6
    assert furniture.footprint('bed', width=6)[0] == 6


def test_the_ontology_matches_the_catalogue_and_the_generators():
    pytest.importorskip('rdflib')
    from rdflib import RDF, RDFS, Graph, Namespace
    build = Namespace('https://alessionar.github.io/open-brick-designer/ns/build/')
    furn = Namespace('https://alessionar.github.io/open-brick-designer/ns/furniture/')
    obd = Namespace('https://alessionar.github.io/open-brick-designer/ns/obd#')
    g = Graph()
    for name in ('obd.ttl', 'furniture.ttl'):
        g.parse(ROOT / 'ontology' / name, format='turtle')
    catalogue = furniture.catalogue()
    concepts = {str(s)[len(furn):] for s in g.subjects(RDF.type, Namespace('http://www.w3.org/2004/02/skos/core#').Concept) if str(s).startswith(str(furn))}
    assert concepts == set(catalogue)
    for kind, row in catalogue.items():
        node = furn[kind]
        assert g.value(node, obd.referencePieces).toPython() == row['pieces']
        assert g.value(node, obd.referenceSets).toPython() == row['sets']
        assert str(g.value(node, obd.typicalFootprint)) == 'x'.join(map(str, row['footprint_studs']))
        assert g.value(node, obd.typicalHeight).toPython() == row['height_plates']
        assert [str(o) for o in sorted(g.objects(node, obd.vocabulary))] == sorted(p['part'] for p in row['parts'][:8])
    for gid, kinds in furniture.GENERATOR_KINDS.items():
        gen, node = generators.get(gid), build[gid]
        assert (node, RDF.type, obd.Furniture) not in g and any((node, RDF.type, c) in g for c in (obd.Seat, obd.Table, obd.Bed, obd.Shelving)), gid
        assert str(g.value(node, RDFS.comment)) == gen.description
        assert {str(o)[len(furn):] for o in g.objects(node, obd.drawnFrom)} == set(kinds)
        assert (g.value(node, obd.furnitureWidth).toPython(), g.value(node, obd.furnitureDepth).toPython(),
                g.value(node, obd.furnitureHeight).toPython()) == furniture.footprint(gid)
    defaults = {'bed': ('bedWidth', 'width'), 'dining-table': ('tableLegCourses', 'leg_courses'), 'bookshelf': ('shelfShelves', 'shelves'),
                'sofa': ('sofaDepth', 'depth'), 'stool': ('stoolCourses', 'courses'), 'chair': ('chairBackCourses', 'back_courses')}
    for gid, (iri, key) in defaults.items():
        assert g.value(build[gid], obd[iri]).toPython() == generators.get(gid).defaults()[key], gid
        spec = generators.get(gid).params[key]
        assert g.value(obd[iri], obd.default).toPython() == spec['default']
        assert str(g.value(obd[iri], Namespace('http://purl.org/dc/terms/').identifier)) == key


def test_the_json_catalogue_is_plain_data():
    doc = json.loads(furniture.CATALOGUE.read_text(encoding='utf-8'))
    assert set(doc) == {'description', 'kinds'}
