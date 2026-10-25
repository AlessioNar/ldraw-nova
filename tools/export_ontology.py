"""Publish ontology/*.ttl as docs/ns/, the folder served under the base IRI (issue #97, epic #89).

    python tools/export_ontology.py           rewrite docs/ns/
    python tools/export_ontology.py --check   exit 1 if docs/ns/ is out of date (CI)

Needs rdflib (toolkit-only: `pip install rdflib`); the app never imports this. Each source file is re-serialised
byte-stably (blank nodes relabelled canonically, subjects sorted, no timestamps), the files are also merged into one
ontology.ttl for loading in a single call, and index.html links them. GitHub Pages serves docs/, so
https://alessionar.github.io/open-brick-designer/ns/obd.ttl is docs/ns/obd.ttl.
"""
from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Graph
from rdflib.compare import to_canonical_graph

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'ontology'
OUT = ROOT / 'docs' / 'ns'
BASE = 'https://alessionar.github.io/open-brick-designer/ns/'
FILES = {
    'obd.ttl': 'Component classes and parameters (OWL)',
    'roles.ttl': 'Role vocabulary (SKOS)',
    'shapes.ttl': 'Validity rules (SHACL)',
    'sets.ttl': 'Sets, brands and parts-list sources (OWL, SKOS, PROV-O)',
}
MERGED = 'ontology.ttl'
PREFIXES = {
    'obd': BASE + 'obd#', 'role': BASE + 'role/', 'build': BASE + 'build/', 'brand': BASE + 'brand/', 'psrc': BASE + 'partssource/',
    'prov': 'http://www.w3.org/ns/prov#',
    'owl': 'http://www.w3.org/2002/07/owl#', 'skos': 'http://www.w3.org/2004/02/skos/core#',
    'sh': 'http://www.w3.org/ns/shacl#', 'dcterms': 'http://purl.org/dc/terms/',
}


def serialise(g):
    out = Graph()
    for prefix, iri in PREFIXES.items():
        out.bind(prefix, iri)
    for t in to_canonical_graph(g):
        out.add(t)
    return out.serialize(format='turtle')


def render():
    """File name -> text, for every published file."""
    graphs = {name: Graph().parse(SRC / name, format='turtle') for name in FILES}
    files = {name: serialise(g) for name, g in graphs.items()}
    merged = Graph()
    for g in graphs.values():
        merged += g
    files[MERGED] = serialise(merged)
    links = ''.join(f'<li><a href="{n}">{n}</a>: {d}</li>' for n, d in FILES.items())
    files['index.html'] = (
        '<!doctype html><meta charset="utf-8"><title>Open Brick Designer ontology</title>\n'
        '<h1>Open Brick Designer ontology</h1>\n'
        f'<p>Licence: <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>. Namespace <code>{BASE}</code>.'
        ' Generated from <code>ontology/</code> by <code>tools/export_ontology.py</code>.</p>\n'
        f'<ul>{links}<li><a href="{MERGED}">{MERGED}</a>: all of them in one file</li></ul>\n'
    )
    return files


def main(argv):
    files = render()
    if '--check' in argv:
        stale = [n for n, text in files.items() if not (OUT / n).is_file() or (OUT / n).read_text(encoding='utf-8') != text]
        if stale:
            print(f'docs/ns/ is out of date ({", ".join(stale)}): run python tools/export_ontology.py')
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
