"""Furniture catalogue: what the reference sets use for chairs, tables, beds and the like, and how big each piece is.

The catalogue (data/furniture-catalogue.json) is derived by tools/harvest_furniture.py from the furniture submodels of the
reference models. The furniture generators (generators/furniture.py) draw their parts from it; a room places a piece by its
footprint:

    from ldraw_tools import furniture
    furniture.footprint('table', width=6, depth=4)      # (6, 4, 64): studs x, studs z, height in LDU
    furniture.piece('bed', width=4)                      # generator result: plan + resolved params + notes

Every piece is built with its bottom at Y=0, centred on x and z, with the front (the side you sit or stand on) at -Z.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

CATALOGUE = Path(__file__).with_name('data') / 'furniture-catalogue.json'

# generator id -> catalogue kind(s) whose parts and proportions it is drawn from
GENERATOR_KINDS = {
    'stool': ('stool',), 'chair': ('chair', 'armchair'), 'dining-table': ('table', 'desk'),
    'bed': ('bed',), 'sofa': ('sofa', 'bench'), 'bookshelf': ('shelf', 'cabinet'),
}


@cache
def catalogue():
    """The harvested catalogue: {kind: {pieces, sets, footprint_studs, height_plates, parts, example_sets}}."""
    return json.loads(CATALOGUE.read_text(encoding='utf-8'))['kinds']


def vocabulary(generator_id):
    """Part numbers the reference sets use for this kind of furniture."""
    return {row['part'] for kind in GENERATOR_KINDS[generator_id] for row in catalogue()[kind]['parts']}


def footprint(generator_id, **params):
    """(studs x, studs z, height in LDU) of a piece with these generator parameters, without building it."""
    from .generators import get
    resolved = get(generator_id).resolve(params)
    return SIZES[generator_id](resolved)


def piece(generator_id, **params):
    """Build a piece through its generator."""
    from .generators import generate
    return generate(generator_id, params)


SIZES = {
    'stool': lambda p: (2, 2, 24 * p['courses'] + 8),
    'chair': lambda p: (2, 2, 32 + 24 * p['back_courses']),
    'dining-table': lambda p: (p['width'], p['depth'], 24 * p['leg_courses'] + 16),
    'bed': lambda p: (p['width'], p['length'], 88),
    'sofa': lambda p: (p['width'], p['depth'], 64),
    'bookshelf': lambda p: (p['width'], 2, 8 + 56 * p['shelves']),
}
