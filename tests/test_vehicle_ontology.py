"""ontology/vehicles.ttl describes vehicles and spaceships; its first implementations must match the generators' own tables."""
from pathlib import Path

import pytest

pytest.importorskip('rdflib')
from rdflib import RDF, RDFS, Graph, Namespace  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OBD = Namespace('https://alessionar.github.io/open-brick-designer/ns/obd#')
BUILD = Namespace('https://alessionar.github.io/open-brick-designer/ns/build/')
ROLE = Namespace('https://alessionar.github.io/open-brick-designer/ns/role/')
DCT = Namespace('http://purl.org/dc/terms/')
PROFILE_CLASS = {'road': OBD.RoadVehicle, 'rail': OBD.RailVehicle, 'motorcycle': OBD.Motorcycle, 'watercraft': OBD.Watercraft, 'aircraft': OBD.Aircraft}


@pytest.fixture(scope='module')
def g():
    graph = Graph()
    for name in ('obd.ttl', 'roles.ttl', 'vehicles.ttl'):
        graph.parse(ROOT / 'ontology' / name, format='turtle')
    return graph


def test_every_design_is_a_vehicle_with_the_generators_numbers(g):
    from ldraw_tools.vehicles import DESIGNS
    for key, d in DESIGNS.items():
        node = BUILD[key]
        assert (node, RDF.type, PROFILE_CLASS[d.get('profile', 'road')]) in g, key
        assert str(g.value(node, DCT.title)) == d['title']
        assert str(g.value(node, OBD.vehiclePalette)) == d['palette']
        assert g.value(node, OBD.vehicleLength).toPython() == d['length']
        assert str(g.value(node, RDFS.comment)) == d['lesson']
        if 'wheelbase' in d:
            assert g.value(node, OBD.vehicleWheelbase).toPython() == d['wheelbase']


def test_every_archetype_is_a_spaceship(g):
    from ldraw_tools.spaceships import ARCHETYPES
    for key, a in ARCHETYPES.items():
        node = BUILD[key]
        assert (node, RDF.type, OBD.Spaceship) in g, key
        assert str(g.value(node, OBD.spaceshipArchetype)) == key
        assert str(g.value(node, DCT.title)) == a['title']
        assert str(g.value(node, RDFS.comment)) == a['silhouette']


def test_the_train_and_ship_generators_are_vehicles_with_their_defaults(g):
    from ldraw_tools import generators
    train, ship = generators.get('toy-train'), generators.get('cargo-ship')
    for gen, profile in ((train, 'rail'), (ship, 'watercraft')):
        node = BUILD[gen.id]
        assert (node, RDF.type, PROFILE_CLASS[profile]) in g, gen.id
        assert str(g.value(node, DCT.title)) == gen.title
        assert str(g.value(node, RDFS.comment)) == gen.description
    wagons = train.defaults()['wagons']
    assert g.value(BUILD['toy-train'], OBD.trainWagons).toPython() == wagons
    from ldraw_tools.generators.transport import LOCO_LENGTH, WAGON_LENGTH
    assert g.value(BUILD['toy-train'], OBD.vehicleLength).toPython() == LOCO_LENGTH + WAGON_LENGTH * wagons
    assert g.value(BUILD['cargo-ship'], OBD.vehicleLength).toPython() == ship.defaults()['length']
    assert g.value(BUILD['cargo-ship'], OBD.shipBays).toPython() == ship.defaults()['bays']


def test_no_extra_builds(g):
    from ldraw_tools.spaceships import ARCHETYPES
    from ldraw_tools.vehicles import DESIGNS
    ours = {str(s)[len(BUILD):] for s in g.subjects(RDF.type, None) if str(s).startswith(str(BUILD))}
    assert ours == set(DESIGNS) | set(ARCHETYPES) | {'toy-train', 'cargo-ship'}


def test_wheel_packs_are_the_generators(g):
    from rdflib.collection import Collection
    from rdflib.namespace import OWL
    from ldraw_tools.vehicles import WHEEL_PACKS
    rng = g.value(OBD.runningGearPack, RDFS.range)
    assert [str(v) for v in Collection(g, g.value(rng, OWL.oneOf))] == list(WHEEL_PACKS)


def test_vehicle_components_stamp_existing_roles(g):
    from ldraw_tools.roles import CANONICAL  # noqa: F401  (import proves the classifier module loads)
    comps = list(g.subjects(RDFS.subClassOf, OBD.VehicleComponent))
    assert len(comps) == 6
    for c in comps:
        assert (c, OBD.partOf, OBD.Vehicle) in g
        roles = list(g.objects(c, OBD.role))
        assert roles
        for r in roles:
            assert (r, RDF.type, Namespace('http://www.w3.org/2004/02/skos/core#').Concept) in g


def test_shapes_accept_the_designs_and_reject_an_odd_wheelbase(g):
    pyshacl = pytest.importorskip('pyshacl')
    conforms, _, text = pyshacl.validate(g, shacl_graph=g, inference='none')
    assert conforms, text
    bad = Graph()
    bad += g
    bad.set((BUILD['grand-tourer'], OBD.vehicleWheelbase, __import__('rdflib').Literal(11)))
    conforms, _, text = pyshacl.validate(bad, shacl_graph=g, inference='none')
    assert not conforms and 'even-studs' in text
