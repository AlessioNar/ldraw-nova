"""Validate a grammar configuration against ontology/shapes.ttl (SHACL, issue #93).

    python tools/validate_shapes.py --presets      validate every preset's normalised config (CI)
    python tools/validate_shapes.py config.json    validate one config (normalised first, so it is complete)

Needs rdflib and pyshacl (toolkit-only: `pip install rdflib pyshacl`); the app never imports this. A config becomes a graph by the
vocabulary of ontology/obd.ttl: a Building node, one component node per group (obd:partOf the building), and a triple per parameter
whose predicate is the parameter with that dcterms:identifier in that group. The shapes judge the normalised config: normalise()
repairs what it can, the shapes state what must hold afterwards.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pyshacl import validate
from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OBD = Namespace('https://alessionar.github.io/open-brick-designer/ns/obd#')
DCT = Namespace('http://purl.org/dc/terms/')
SH = Namespace('http://www.w3.org/ns/shacl#')
INSTANCE = Namespace('https://alessionar.github.io/open-brick-designer/ns/build/')

_cache: dict[str, Graph] = {}


def _graph(name):
    if name not in _cache:
        _cache[name] = Graph().parse(ROOT / 'ontology' / f'{name}.ttl', format='turtle')
    return _cache[name]


def _keys():
    """{(group key or '', parameter key): property IRI} read from obd.ttl."""
    g = _graph('obd')
    groups = {c: str(g.value(c, DCT.identifier)) for c in g.subjects(RDFS.subClassOf, OBD.Component)}
    out = {('', 'name'): OBD.buildingName}
    for prop in g.subjects(RDFS.domain, None):
        dom = g.value(prop, RDFS.domain)
        if dom in groups and g.value(prop, DCT.identifier) is not None:
            out[(groups[dom], str(g.value(prop, DCT.identifier)))] = prop
    return out, {key: cls for cls, key in groups.items()}


def config_graph(config):
    """The RDF graph of a complete (normalised) config."""
    keys, classes = _keys()
    g = Graph()
    building = INSTANCE['building']
    g.add((building, RDF.type, OBD.Building))
    for key, value in config.items():
        if isinstance(value, dict):
            node = INSTANCE[key]
            g.add((node, RDF.type, classes[key]))
            g.add((node, OBD.partOf, building))
            for name, v in value.items():
                for item in (v if isinstance(v, list) else [v]):
                    g.add((node, keys[(key, name)], _literal(item)))
        else:
            g.add((building, keys[('', key)], _literal(value)))
    return g


def _literal(value):
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    return Literal(value)


def check(config, repair=True):
    """[(rule id or shape name, message)] for the rules a config breaks; [] when it conforms.

    repair=True normalises first (the shapes judge what the builder would get); repair=False only fills defaults, so the
    shapes see the rules the raw config breaks.
    """
    from ldraw_tools.grammar.schema import defaults, deep_merge, normalise
    cfg = normalise(config)[0] if repair else deep_merge(defaults(), config)
    conforms, report, _ = validate(config_graph(cfg), shacl_graph=_graph('shapes'), ont_graph=_graph('obd'), inference='none')
    if conforms:
        return []
    shapes = _graph('shapes')
    out = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        shape = report.value(result, SH.sourceShape)
        rule = shapes.value(shape, DCT.identifier)
        out.append((str(rule) if rule is not None else str(shape), str(report.value(result, SH.resultMessage))))
    return out


def main(argv):
    if argv == ['--presets']:
        from ldraw_tools.grammar.presets import PRESETS
        configs = {name: p['config'] for name, p in PRESETS.items()}
    elif len(argv) == 1:
        configs = {argv[0]: json.loads(Path(argv[0]).read_text(encoding='utf-8'))}
    else:
        print(__doc__)
        return 2
    failed = 0
    for name, config in configs.items():
        for _rule, message in check(config):
            failed += 1
            print(f'{name}: {message}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
