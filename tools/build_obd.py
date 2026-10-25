"""Read ontology/obd.ttl (the source of truth for build components and parameters, issue #92) as JSON-Schema-shaped data.

    python tools/build_obd.py --check   exit 1 if obd.ttl disagrees with ldraw_tools/grammar/schema.py (CI)

Needs rdflib (toolkit-only: `pip install rdflib`); the app never imports this. Until #94 generates the schema from the
ontology, the hand-kept SCHEMA must say exactly what obd.ttl says: components, parameters, ranges, defaults, colour flag.
Mapping: obd:Component subclass -> x-component group (dcterms:identifier); datatype property with rdfs:domain -> parameter
(dcterms:identifier = JSON key); rdfs:range -> type, enum, minimum/maximum; obd:default -> default; obd:order -> property order;
obd:ColourParameter -> x-colour; rdfs:comment -> description; obd:role -> the roles a component stamps.
"""
from __future__ import annotations

import sys
from pathlib import Path

from rdflib import RDF, RDFS, XSD, BNode, Graph, Literal, Namespace
from rdflib.collection import Collection

ROOT = Path(__file__).resolve().parent.parent
TTL = ROOT / 'ontology' / 'obd.ttl'
OBD = Namespace('https://alessionar.github.io/open-brick-designer/ns/obd#')
OWL = Namespace('http://www.w3.org/2002/07/owl#')
DCT = Namespace('http://purl.org/dc/terms/')
ROLE = Namespace('https://alessionar.github.io/open-brick-designer/ns/role/')


def _value(node):
    return node.toPython() if isinstance(node, Literal) else str(node)


def _list(g, head):
    return [_value(n) for n in Collection(g, head)]


def _spec(g, prop):
    """One parameter as the JSON Schema fragment the hand-kept SCHEMA holds."""
    rng = g.value(prop, RDFS.range)
    spec = {}
    if rng == XSD.boolean:
        spec['type'] = 'boolean'
    elif rng == OBD.LdrawColour:
        spec.update({'type': ['string', 'integer'], 'x-colour': True})
    elif isinstance(rng, BNode) and g.value(rng, OWL.oneOf) is not None:
        values = _list(g, g.value(rng, OWL.oneOf))
        spec.update({'type': 'array', 'uniqueItems': True, 'items': {'enum': values}} if (prop, RDF.type, OWL.FunctionalProperty) not in g
                    else {'enum': values})
    elif isinstance(rng, BNode):
        bounds = {str(p): _value(v) for item in Collection(g, g.value(rng, OWL.withRestrictions)) for p, v in g.predicate_objects(item)}
        if g.value(rng, OWL.onDatatype) == XSD.string:
            spec.update({'type': 'string', 'maxLength': bounds[str(XSD.maxLength)], 'pattern': bounds[str(XSD.pattern)]})
        else:
            spec.update({'type': 'integer', 'minimum': bounds[str(XSD.minInclusive)], 'maximum': bounds[str(XSD.maxInclusive)]})
    else:
        spec['type'] = 'string'
    default = g.value(prop, OBD.default)
    spec['default'] = _list(g, default) if (default, RDF.first, None) in g else _value(default)
    spec['description'] = str(g.value(prop, RDFS.comment))
    return dict(sorted(spec.items()))


def load(path=TTL):
    """-> ({group: {'component', 'description', 'roles', 'properties': {key: spec}}}, {key: spec} for the Building itself)."""
    g = Graph().parse(path, format='turtle')

    def params(domain):
        props = sorted(g.subjects(RDFS.domain, domain), key=lambda p: int(g.value(p, OBD.order)))
        return {str(g.value(p, DCT.identifier)): _spec(g, p) for p in props if (p, RDF.type, OWL.DatatypeProperty) in g}

    comps = sorted(g.subjects(RDFS.subClassOf, OBD.Component), key=lambda c: int(g.value(c, OBD.order)))
    groups = {str(g.value(c, DCT.identifier)): {
        'component': str(g.value(c, RDFS.label)), 'description': str(g.value(c, RDFS.comment)),
        'roles': sorted(str(r)[len(ROLE):] for r in g.objects(c, OBD.role)), 'properties': params(c)} for c in comps}
    return groups, params(OBD.Building)


def diff(groups, building):
    """Differences between obd.ttl and ldraw_tools.grammar.schema.SCHEMA, as readable lines."""
    from ldraw_tools.grammar.schema import SCHEMA
    out = []
    props = SCHEMA['properties']
    objects = {k: v for k, v in props.items() if v.get('type') == 'object'}
    if list(objects) != list(groups):
        out.append(f'components differ: schema {list(objects)}, ontology {list(groups)}')
    for key, comp in groups.items():
        s = objects.get(key)
        if s is None:
            continue
        if (s['x-component'], s['description']) != (comp['component'], comp['description']):
            out.append(f'{key}: component name or description differs')
        if list(s['properties']) != list(comp['properties']):
            out.append(f'{key}: parameters differ: schema {list(s["properties"])}, ontology {list(comp["properties"])}')
        for name, spec in s['properties'].items():
            want = {k: v for k, v in spec.items() if k != 'x-colour' or v}
            if comp['properties'].get(name) != dict(sorted(want.items())):
                out.append(f'{key}.{name}: schema {dict(sorted(want.items()))}, ontology {comp["properties"].get(name)}')
    top = {k: v for k, v in props.items() if v.get('type') != 'object'}
    if building != {k: dict(sorted(v.items())) for k, v in top.items()}:
        out.append('Building parameters differ (name)')
    return out


def main(argv):
    problems = diff(*load())
    if problems:
        print('ontology/obd.ttl and ldraw_tools/grammar/schema.py disagree:\n  ' + '\n  '.join(problems))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
