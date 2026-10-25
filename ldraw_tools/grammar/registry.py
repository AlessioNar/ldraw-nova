"""The grammar as one registry entry, id "building"."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..generators import house
from ..generators.base import Generator
from . import compat, engine
from .presets import PRESETS
from .roles import ROLE_NAMES
from .schema import SCHEMA


@dataclass(frozen=True)
class GrammarGenerator(Generator):
    presets: dict = field(default_factory=dict, compare=False)

    def entry(self):
        out = super().entry()
        out['presets'] = {k: dict(title=v['title'], description=v['description'], keywords=list(v['keywords']),
                                  config=copy.deepcopy(v['config']))
                          for k, v in self.presets.items()}
        return out


def _build(params):
    from .schema import deep_merge
    config, alias = compat.split(params)
    notes = []
    if alias:
        patch, notes = compat.translate(alias, keep_storeys=config.get('storeys') not in (None, GENERATOR.defaults().get('storeys')))
        config = deep_merge(config, patch)      # house-dialect values win over the group defaults
    result = engine.build(config)
    return result['plan'], notes + result['notes']


GENERATOR = GrammarGenerator(
    id='building', title='Building (grammar)',
    # Routing: `building` answers every house, cottage, shop and townhouse request (the old house generator stays
    # registered as `house` for saved builds) and the new types (castle, tower, pagoda, barn...).
    families=('house', 'building'),
    kinds=tuple(sorted({k for p in PRESETS.values() for k in p['keywords']} | set(house.GENERATOR.kinds)
                       | {'manor', 'hall', 'townhall', 'fort', 'villa', 'home'})),
    description='One grammar for buildings: footprint (rect, L, U, courtyard), storeys, wall style, openings, corner '
                'towers, roof (gable, hip, flat, battlements, eave roofs), details. Also accepts the house generator\'s '
                'flat parameters. Presets: ' + ', '.join(PRESETS) + '.',
    params={**{k: v for k, v in SCHEMA['properties'].items()}, **compat.ALIASES},
    roles=ROLE_NAMES,
    build=_build,
    coverage=('rect/L/U/courtyard', 'storeys', 'brick/timber/stone', 'doors', 'windows', 'arrow slits', 'towers',
              'gable/hip/flat', 'battlements', 'chimney', 'porch', 'balcony', 'sign', 'base and path'),
    quality='Role-tagged, running-bond walls with real window and door parts; one connected component by construction.',
    presets=PRESETS,
)
