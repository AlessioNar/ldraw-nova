"""Generator registry: deterministic builders with one interface (see docs/plans/done/deterministic-first/PROPOSAL.md).

    from ldraw_tools.generators import get, digest
    result = get('house').generate({'width': 8, 'depth': 6, 'windows': 2})   # plan + resolved params + notes

Every generator is a pure function of its parameters, so (id, params) is a valid cache key.
"""
from __future__ import annotations

from . import building, house, recipes, scenery, spaceship, vehicles
from .base import Generator, digest as _digest, tag_roles  # noqa: F401
from .colours import colour_code, colour_names  # noqa: F401

REGISTRY = {g.id: g for g in [building.GENERATOR, house.GENERATOR, *vehicles.GENERATORS, spaceship.GENERATOR, *recipes.GENERATORS, scenery.GENERATOR]}


def get(generator_id):
    if generator_id not in REGISTRY:
        raise ValueError(f'Unknown generator {generator_id!r}; available: {", ".join(REGISTRY)}')
    return REGISTRY[generator_id]


def generate(generator_id, params=None):
    return get(generator_id).generate(params)


def find(family=None, kind=None):
    """Generators for a spec family and/or kind synonym, best first (kind match, then family match)."""
    kind = (kind or '').casefold()
    rows = []
    for g in REGISTRY.values():
        score = (family in g.families) * 2 + any(kind and (kind == k or kind in k or k in kind) for k in g.kinds) * 3
        if score:
            rows.append((-score, g.id))
    return [REGISTRY[i] for _, i in sorted(rows)]


def digest():
    """Compact registry text for the understanding prompt (stays under 1,500 tokens)."""
    return _digest(REGISTRY.values())
