"""The one role vocabulary: what a placement is FOR, independent of its part number or colour.

The building grammar stamps roles at construction, the generators stamp them, and the classifier (`roles.py`) guesses
them for everything else. All three read this module, so a role means the same thing in every model and an edit such as
"make the roof blue" selects the same placements whichever of them produced the plan.

The data is plain (names, descriptions, words, a name -> broader name map) so it can be exported later as a SKOS
concept scheme: `ROLES` are the concepts, `ALIAS` the `skos:broader` links, `WORDS` the `skos:altLabel`s.
"""
from __future__ import annotations

ROLES = {   # role -> what it covers
    'roof': 'Roof surface: slopes, ridge caps, flat-roof deck, tower cones',
    'wall': 'Load-bearing wall bricks, gable end walls, parapet merlons',
    'window': 'Window frames and arch/slit infill that belongs to a window',
    'door': 'Door frame and door leaf, gates',
    'glass': 'Transparent panes',
    'base': 'Baseplate, ground, paths',
    'wheel': 'Wheels and rims',
    'tyre': 'Tyres',
    'chassis': 'Vehicle frame, axles, undercarriage',
    'body': 'Vehicle or craft body: cabin, hull, fuselage',
    'wing': 'Wings and tail surfaces',
    'engine': 'Engines, thrusters, propellers',
    'tower': 'Corner tower bodies (round bricks, tower walls) and tower caps; a kind of wall, see ALIAS',
    'detail': 'Chimney, porch, balcony, steps, sign, shutters, flower boxes, fences, crenellation extras',
    'trim': 'Bands between storeys, cornices, corner pillars, plinth, window sills, keystones',
    'structure': 'Interior floors and hidden supports',
}

# Roles the building grammar stamps (the others are the classifier's and the generators').
GRAMMAR_ROLES = ('roof', 'wall', 'window', 'door', 'glass', 'base', 'tower', 'detail', 'trim', 'structure')

# A role name that is an alias of a broader role (a generator's own word, or a specialisation) -> its canonical role.
# `tower` is a kind of wall: the grammar stamps it so that "the towers" can be addressed on their own, while anything
# comparing roles across sources (the classifier's scores, role-based edits) folds it into `wall`.
ALIAS = {'porch': 'trim', 'chimney': 'roof', 'accent': 'trim', 'light': 'detail', 'hull': 'body',
         'canopy': 'glass', 'wood': 'detail', 'metal': 'trim', 'headlamp': 'detail', 'tail_lamp': 'detail',
         'tower': 'wall'}

# What a person says -> role (used by role_edits to resolve "the roof", "the windows", "the wheels").
# A feature word such as "chimney" or "porch" is deliberately absent: it only means something on a plan that has
# that role (a generator's or the grammar's), and "remove the chimney" must never fall back to "remove the roof".
WORDS = {
    'roof': 'roof', 'roofs': 'roof', 'roofing': 'roof', 'wall': 'wall', 'walls': 'wall', 'brickwork': 'wall',
    'masonry': 'wall', 'tower': 'tower', 'towers': 'tower', 'turret': 'tower', 'turrets': 'tower', 'panes': 'glass',
    'glazing': 'glass', 'baseplate': 'base', 'ground': 'base', 'cornice': 'trim', 'plinth': 'trim', 'window': 'window',
    'windows': 'window', 'glass': 'glass', 'windscreen': 'glass', 'windshield': 'glass', 'canopy': 'glass',
    'door': 'door', 'doors': 'door', 'floor': 'base', 'base': 'base', 'foundation': 'base', 'wheel': 'wheel',
    'wheels': 'wheel', 'rim': 'wheel', 'rims': 'wheel', 'tyre': 'tyre', 'tyres': 'tyre', 'tire': 'tyre',
    'tires': 'tyre', 'chassis': 'chassis', 'frame': 'chassis', 'body': 'body', 'bodywork': 'body', 'wing': 'wing',
    'wings': 'wing', 'engine': 'engine', 'engines': 'engine', 'trim': 'trim', 'details': 'detail', 'detail': 'detail',
}

# The grammar's natural-language phrasing of its roles ("make the tiles blue"), feature words included, because a
# grammar plan always has those features' roles.
GRAMMAR_WORDS = {
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


def canonical(role):
    """The broader role a role name stands for ('tower' -> 'wall', 'hull' -> 'body'); any other name is its own."""
    return ALIAS.get(role, role)


def role_for_word(word):
    """Role for a word from the grammar's phrasing ('tiles' -> 'roof'), or None."""
    word = word.strip().casefold()
    for role, words in GRAMMAR_WORDS.items():
        if word in words:
            return role
    return None
