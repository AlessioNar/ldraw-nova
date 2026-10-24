"""The grammar as one registry entry, id "building"."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..generators import house
from ..generators.base import Generator
from . import engine
from .presets import PRESETS
from .roles import ROLE_NAMES
from .schema import SCHEMA


@dataclass(frozen=True)
class GrammarGenerator(Generator):
    presets: dict = field(default_factory=dict, compare=False)

    def entry(self):
        out = super().entry()
        out['presets'] = {k: dict(title=v['title'], description=v['description'], config=copy.deepcopy(v['config']))
                          for k, v in self.presets.items()}
        return out


def _build(params):
    result = engine.build(params)
    return result['plan'], result['notes']


GENERATOR = GrammarGenerator(
    id='building', title='Building (grammar)',
    # Routing: the old house generator keeps the words it already owns (house, cottage, shop...), so the fast path
    # still reaches it; the grammar takes the new ones. Flipping that is a one-line change once it has earned it.
    families=('castle', 'tower', 'building-grammar'),
    kinds=tuple(sorted(k for k in {k for p in PRESETS.values() for k in p['keywords']} | {'manor', 'hall', 'townhall', 'fort'}
                       if not any(k == o or k in o or o in k for o in house.GENERATOR.kinds))),
    description='One grammar for buildings: footprint (rect, L, U, courtyard), storeys, wall style, openings, corner '
                'towers, roof (gable, hip, flat, battlements), details. Presets: ' + ', '.join(PRESETS) + '.',
    params={k: v for k, v in SCHEMA['properties'].items()},
    roles=ROLE_NAMES,
    build=_build,
    coverage=('rect/L/U/courtyard', 'storeys', 'brick/timber/stone', 'doors', 'windows', 'arrow slits', 'towers',
              'gable/hip/flat', 'battlements', 'chimney', 'porch', 'balcony', 'sign', 'base and path'),
    quality='Role-tagged, running-bond walls with real window and door parts; one connected component by construction.',
    presets=PRESETS,
)
