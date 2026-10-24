"""Templates from builds: any model with roles becomes a parametric starting point (see docs/plans/build-templates).

A sibling of the generator registry with the same surface, so the understanding step can route to either:

    from ldraw_tools import templates
    templates.find('house', 'cottage')            # templates for a spec family/kind, best first
    result = templates.generate('cottage', {'colour_roof': 'blue', 'x_delta': 4})   # plan + resolved params + notes
    templates.digest()                             # compact text for the understanding prompt

Prototype templates ship in ldraw_tools/data/templates/*.json; a person's own live under a data directory passed to
`load_dir` (the workspace's "Make this a template" writes `derive.derive(...)` output there).
"""
from __future__ import annotations

from pathlib import Path

from ..generators.base import digest_line
from . import core
from .core import Template, from_data, load  # noqa: F401

BUNDLED = Path(__file__).resolve().parents[1] / 'data' / 'templates'
REGISTRY: dict = {}


def load_dir(directory):
    """Add every *.json template found in a directory to the registry; returns the ids added."""
    added = []
    for path in sorted(Path(directory).glob('*.json')):
        template = load(path)
        REGISTRY[template.id] = template
        added.append(template.id)
    return added


def register(data_or_template):
    template = data_or_template if isinstance(data_or_template, Template) else from_data(data_or_template)
    REGISTRY[template.id] = template
    return template


def get(template_id):
    if template_id not in REGISTRY:
        raise ValueError(f'Unknown template {template_id!r}; available: {", ".join(REGISTRY)}')
    return REGISTRY[template_id]


def generate(template_id, params=None):
    return get(template_id).generate(params)


def find(family=None, kind=None):
    """Templates for a spec family and/or kind synonym, best first (same scoring as generators.find)."""
    kind = (kind or '').casefold()
    rows = []
    for t in REGISTRY.values():
        score = (family in t.families) * 2 + any(kind and (kind == k or kind in k or k in kind) for k in t.kinds) * 3
        if score:
            rows.append((-score, t.id))
    return [REGISTRY[i] for _, i in sorted(rows)]


def candidates(family=None, kind=None):
    """Generators and templates for a spec, best first: [{source, id, score}].

    A kind match beats a family match; on a tie a generator wins (it is parametric from scratch and tested at its corners),
    and a template wins over a generator only for a kind only it covers (a named real model, the person's own build).
    """
    from .. import generators
    kind = (kind or '').casefold()
    rows = []
    for source, registry in (('generator', generators.REGISTRY), ('template', REGISTRY)):
        for item in registry.values():
            near = any(kind and (kind in k or k in kind) for k in item.kinds)
            score = (family in item.families) * 2 + (4 if kind in item.kinds else 3 if near else 0)
            if score:
                rows.append(dict(source=source, id=item.id, score=score))
    return sorted(rows, key=lambda r: (-r['score'], r['source'] != 'generator', r['id']))


def digest():
    """Compact registry text for the understanding prompt, one line per template."""
    return '\n'.join(digest_line(t) + ' | template' for t in REGISTRY.values())


if BUNDLED.is_dir():
    load_dir(BUNDLED)
