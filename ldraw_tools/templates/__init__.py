"""Templates from builds: any model with roles becomes a parametric starting point (see docs/plans/active/build-templates).

A sibling of the generator registry with the same surface, so the understanding step can route to either:

    from ldraw_tools import templates
    templates.find('house', 'cottage')            # templates for a spec family/kind, best first
    result = templates.generate('cottage', {'colour_roof': 'blue', 'x_delta': 4})   # plan + resolved params + notes
    templates.digest()                             # compact text for the understanding prompt

Prototype templates ship in ldraw_tools/data/templates/*.json; a person's own live under a data directory passed to
`load_dir` (the workspace's "Make this a template" writes `derive.derive(...)` output there).
"""
from __future__ import annotations

import os
import types
from pathlib import Path

from ..generators.base import digest_line
from .core import Template, from_data, load  # noqa: F401

BUNDLED = Path(__file__).resolve().parents[1] / 'data' / 'templates'
USER_DIR_ENV = 'LDRAW_NOVA_TEMPLATES_DIR'        # the person's own templates (the backend's data directory); optional
REGISTRY: dict = {}
_user: dict = {}                                 # id -> file of the templates loaded from the user's folder


def load_dir(directory):
    """Add every *.json template found in a directory to the registry; returns the ids added."""
    added = []
    for path in sorted(Path(directory).glob('*.json')):
        if path.name.endswith('.report.json'):
            continue
        template = load(path)
        REGISTRY[template.id] = template
        added.append(template.id)
    return added


def user_dir():
    value = os.environ.get(USER_DIR_ENV)
    return Path(value) if value else None


def refresh():
    """Re-read the person's templates folder: what was saved since the last call appears, what was deleted goes.
    A long-lived worker calls this before it builds from a template. Returns the ids now loaded from the folder."""
    directory = user_dir()
    found = {}
    if directory is not None and directory.is_dir():
        for path in sorted(directory.glob('*.json')):
            if path.name.endswith('.report.json'):
                continue
            try:
                template = load(path)
            except (OSError, ValueError, KeyError):
                continue                         # a half-written or foreign file must not break the others
            if template.id in REGISTRY and template.id not in _user:
                continue                         # a bundled template keeps its id
            REGISTRY[template.id] = template
            found[template.id] = path
    for gone in set(_user) - set(found):
        REGISTRY.pop(gone, None)
    _user.clear()
    _user.update(found)
    return list(found)


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
            nearest = min((abs(len(k) - len(kind)) for k in t.kinds), default=0)      # on a tie, the closest kind wording
            rows.append((-score, nearest, t.id))
    return [REGISTRY[i] for *_, i in sorted(rows)]


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
    return '\n'.join(digest_line(_compact(t)) + ' | template' for t in REGISTRY.values())


def _compact(template):
    """The template as the prompt sees it: sizes and colours only. Swaps and sub-assembly switches stay in the parameter schema
    (a request can still set them) but, listed for every shipped template, they would dominate the understanding prompt."""
    params = {k: v for k, v in template.params.items() if not k.startswith(('swap_', 'include_'))}
    return types.SimpleNamespace(id=template.id, families=template.families, kinds=template.kinds, params=params)


if BUNDLED.is_dir():
    load_dir(BUNDLED)
refresh()
