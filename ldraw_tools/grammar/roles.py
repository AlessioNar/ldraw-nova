"""The role vocabulary: what a placement is FOR, independent of its part number or colour.

Every placement the grammar emits carries exactly one of these in `role`. Edits such as "make the roof blue"
select by role, so a role must mean the same thing in every building.
"""
from __future__ import annotations

ROLES = {
    'roof': 'Roof surface: slopes, ridge caps, flat-roof deck, tower cones',
    'wall': 'Load-bearing wall bricks, gable end walls, parapet merlons',
    'window': 'Window frames and arch/slit infill that belongs to a window',
    'door': 'Door frame and door leaf, gates',
    'glass': 'Transparent panes',
    'base': 'Baseplate, ground, paths',
    'tower': 'Corner tower bodies (round bricks, tower walls) and tower caps',
    'detail': 'Chimney, porch, balcony, steps, sign, crenellation extras',
    'trim': 'Bands between storeys, cornices, corner pillars, plinth',
    'structure': 'Interior floors and hidden supports',
}
ROLE_NAMES = tuple(ROLES)

# Words people use for each role, for natural-language addressing ("make the roof blue").
ALIASES = {
    'roof': ('roof', 'roofing', 'tiles', 'slates', 'thatch'),
    'wall': ('wall', 'walls', 'brickwork', 'masonry', 'stonework', 'bricks'),
    'window': ('window', 'windows', 'window frame', 'window frames'),
    'door': ('door', 'doors', 'gate', 'gates', 'entrance'),
    'glass': ('glass', 'panes', 'glazing'),
    'base': ('base', 'baseplate', 'ground', 'grass', 'lawn', 'path', 'pavement'),
    'tower': ('tower', 'towers', 'turret', 'turrets'),
    'detail': ('detail', 'details', 'chimney', 'porch', 'balcony', 'sign'),
    'trim': ('trim', 'band', 'bands', 'cornice', 'plinth', 'pillars', 'quoins'),
    'structure': ('floor', 'floors', 'structure'),
}


def role_for_word(word):
    word = word.strip().casefold()
    for role, words in ALIASES.items():
        if word in words:
            return role
    return None
