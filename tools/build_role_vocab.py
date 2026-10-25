"""Generate the data block of ldraw_tools/role_vocab.py from ontology/roles.ttl (the source of truth, issue #91).

    python tools/build_role_vocab.py           rewrite the generated block
    python tools/build_role_vocab.py --check   exit 1 if role_vocab.py is out of date (CI)

Needs rdflib (toolkit-only: `pip install rdflib`); the app never imports this.
Mapping: concept -> role; obd:order -> ROLES order; skos:definition -> ROLES text; skos:broader -> ALIAS;
skos:altLabel -> WORDS (classifier phrasing); skos:hiddenLabel -> GRAMMAR_WORDS (grammar phrasing);
role:grammar (skos:OrderedCollection) -> GRAMMAR_ROLES.
"""
from __future__ import annotations

import sys
from pathlib import Path

from rdflib import RDF, Graph, Namespace
from rdflib.collection import Collection

ROOT = Path(__file__).resolve().parent.parent
TTL = ROOT / 'ontology' / 'roles.ttl'
TARGET = ROOT / 'ldraw_tools' / 'role_vocab.py'
BEGIN, END = '# BEGIN GENERATED from ontology/roles.ttl (tools/build_role_vocab.py)\n', '# END GENERATED\n'

SKOS = Namespace('http://www.w3.org/2004/02/skos/core#')
ROLE = Namespace('https://alessionar.github.io/open-brick-designer/ns/role/')
OBD = Namespace('https://alessionar.github.io/open-brick-designer/ns/obd#')


def local(iri):
    return str(iri)[len(ROLE):]


def load(path=TTL):
    g = Graph().parse(path, format='turtle')
    concepts = sorted(g.subjects(RDF.type, SKOS.Concept), key=str)
    order = {c: int(g.value(c, OBD.order)) for c in concepts if g.value(c, OBD.order) is not None}
    roles = {local(c): str(g.value(c, SKOS.definition)) for c in sorted(order, key=order.get)}
    alias = {local(c): local(g.value(c, SKOS.broader)) for c in concepts if g.value(c, SKOS.broader)}
    words = {str(w): local(c) for c in concepts for w in g.objects(c, SKOS.altLabel)}
    grammar_words = {local(c): tuple(sorted(str(w) for w in g.objects(c, SKOS.hiddenLabel)))
                     for c in sorted(order, key=order.get) if (c, SKOS.hiddenLabel, None) in g}
    grammar_roles = tuple(local(c) for c in Collection(g, g.value(ROLE.grammar, SKOS.memberList)))
    return roles, grammar_roles, alias, words, grammar_words


def render(data):
    roles, grammar_roles, alias, words, grammar_words = data
    lines = ['ROLES = {   # role -> what it covers']
    lines += [f'    {r!r}: {d!r},' for r, d in roles.items()]
    lines += ['}', '',
              "# Roles the building grammar stamps (the others are the classifier's and the generators').",
              f'GRAMMAR_ROLES = {grammar_roles!r}', '',
              "# A role name that is a narrower alias (skos:broader) of a canonical role, e.g. 'tower' -> 'wall'.",
              'ALIAS = {']
    lines += [f'    {a!r}: {b!r},' for a, b in sorted(alias.items())]
    lines += ['}', '', '# What a person says (skos:altLabel) -> role, used by role_edits.', 'WORDS = {']
    lines += [f'    {w!r}: {r!r},' for w, r in sorted(words.items())]
    lines += ['}', '', "# The grammar's phrasing of its roles (skos:hiddenLabel), feature words included.", 'GRAMMAR_WORDS = {']
    lines += [f'    {r!r}: {ws!r},' for r, ws in grammar_words.items()]
    lines += ['}']
    return BEGIN + '\n'.join(lines) + '\n' + END


def splice(text, block):
    head, rest = text.split(BEGIN, 1)
    return head + block + rest.split(END, 1)[1]


def main(argv):
    text = TARGET.read_text(encoding='utf-8')
    new = splice(text, render(load()))
    if '--check' in argv:
        if new != text:
            print('ldraw_tools/role_vocab.py is out of date: run python tools/build_role_vocab.py')
            return 1
        return 0
    TARGET.write_text(new, encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
