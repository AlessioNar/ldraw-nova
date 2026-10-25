"""The old house generator's flat parameters, accepted by the building grammar.

The fast path, the settings form and saved builds speak this dialect (width, depth, courses, door_side, windows,
roof_colour...). `translate` turns it into grammar configuration so `building` can answer every request the house
generator could, plus everything the grammar adds. Values the grammar cannot express yet are reported, not dropped silently.
"""
from __future__ import annotations

import math

from .schema import SIDES

DOOR_SIDES = ['left', 'centre', 'right']
DOOR_STYLES = ['traditional', 'glazed', 'hatch']

ALIASES = {
    'width': {'type': 'integer', 'minimum': 6, 'maximum': 24, 'x-even': True, 'description': 'House dialect: front wall width in studs (even)'},
    'depth': {'type': 'integer', 'minimum': 6, 'maximum': 20, 'x-even': True, 'description': 'House dialect: side wall length in studs (even)'},
    'courses': {'type': 'integer', 'minimum': 4, 'maximum': 14, 'description': 'House dialect: wall height in courses (12 or more makes two storeys)'},
    'door_side': {'enum': DOOR_SIDES + ['none'], 'description': 'House dialect: where the door sits on the front'},
    'door_style': {'enum': DOOR_STYLES, 'description': 'House dialect: door style'},
    'windows': {'type': 'integer', 'minimum': 0, 'maximum': 8, 'description': 'House dialect: windows in total, spread over window_sides'},
    'window_sides': {'type': 'array', 'uniqueItems': True, 'items': {'enum': SIDES}, 'description': 'House dialect: facades with windows'},
    'roof_style': {'enum': ['gable', 'flat'], 'description': 'House dialect: roof style'},
    'roof_tiers': {'type': 'integer', 'minimum': 0, 'maximum': 9, 'description': 'House dialect: slope courses (0 = as steep as fits)'},
    'porch': {'type': 'boolean', 'description': 'House dialect: pillared porch over the door'},
    'chimney': {'enum': ['none', 'left', 'right'], 'description': 'House dialect: chimney on the ridge'},
    'dormer': {'type': 'boolean', 'description': 'House dialect: dormer window (not built by the grammar yet; reported)'},
    **{k: {'type': ['string', 'integer'], 'x-colour': True, 'description': d}
       for k, d in (('wall_colour', 'House dialect: walls'), ('trim_colour', 'House dialect: window frames, bands'),
                    ('roof_colour', 'House dialect: roof'), ('door_colour', 'House dialect: door leaf'),
                    ('glass_colour', 'House dialect: window glass'), ('chimney_colour', 'House dialect: chimney'))},
}


def split(params):
    """(grammar params, house-dialect params) from one mixed dict."""
    alias = {k: v for k, v in params.items() if k in ALIASES and v is not None}
    rest = {k: v for k, v in params.items() if k not in ALIASES}
    return rest, alias


def translate(alias):
    """(config patch, notes) for house-dialect parameters."""
    cfg, notes = {}, []

    def put(group, key, value):
        cfg.setdefault(group, {})[key] = value

    if 'width' in alias:
        put('footprint', 'width', alias['width'] + alias['width'] % 2)
    if 'depth' in alias:
        put('footprint', 'depth', alias['depth'] + alias['depth'] % 2)
    if 'courses' in alias:
        c = alias['courses']
        if c >= 12:
            put('storeys', 'count', 2)
            put('storeys', 'courses', c // 2)
        else:
            put('storeys', 'count', 1)
            put('storeys', 'courses', c)
    elif alias:
        put('storeys', 'count', 1)
        put('storeys', 'courses', 6)
    if 'door_side' in alias:
        if alias['door_side'] == 'none':
            put('openings', 'door', 'none')
        else:
            put('openings', 'door_position', alias['door_side'])
    if 'door_style' in alias:
        put('openings', 'door', {'traditional': 'plain', 'glazed': 'glazed', 'hatch': 'smooth'}[alias['door_style']])
    sides = alias.get('window_sides')
    if sides is not None:
        put('openings', 'sides', list(sides))
    if 'windows' in alias:
        n = alias['windows']
        if n == 0:
            put('openings', 'ground_windows', 'none')
            put('openings', 'upper_windows', 'none')
        else:
            per = math.ceil(n / max(1, len(sides if sides is not None else SIDES)))
            put('openings', 'count', per)
    if 'roof_style' in alias:
        put('roof', 'style', alias['roof_style'])
    if 'roof_tiers' in alias:
        put('roof', 'tiers', alias['roof_tiers'])
    if 'porch' in alias:
        put('details', 'porch', alias['porch'])
    if 'chimney' in alias:
        put('details', 'chimney', alias['chimney'])
    if alias.get('dormer'):
        notes.append('dormer is not built by the grammar yet; left out')
    for key, (group, name) in {'wall_colour': ('walls', 'colour'), 'roof_colour': ('roof', 'colour'),
                               'door_colour': ('openings', 'door_colour'), 'glass_colour': ('openings', 'glass_colour'),
                               'chimney_colour': ('details', 'chimney_colour')}.items():
        if key in alias:
            put(group, name, alias[key])
    if 'trim_colour' in alias:
        put('openings', 'frame_colour', alias['trim_colour'])
        put('storeys', 'band_colour', alias['trim_colour'])
    return cfg, notes
