"""The one role vocabulary: what a placement is FOR, independent of its part number or colour.

The building grammar stamps roles at construction, the generators stamp them, and the classifier (`roles.py`) guesses
them for everything else. All three read this module, so a role means the same thing in every model and an edit such as
"make the roof blue" selects the same placements whichever of them produced the plan.

The data below is generated from `ontology/roles.ttl`, a SKOS concept scheme (`ROLES` are the concepts, `ALIAS` the
`skos:broader` links, `SUBROLES` the per-colour sub-roles, `WORDS` the `skos:altLabel`s). Edit the Turtle and run `python tools/build_role_vocab.py`.
"""
from __future__ import annotations

# BEGIN GENERATED from ontology/roles.ttl (tools/build_role_vocab.py)
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

# A role name that is a narrower alias (skos:broader) of a canonical role, e.g. 'tower' -> 'wall'.
ALIAS = {
    'accent': 'trim',
    'canopy': 'glass',
    'chimney': 'roof',
    'headlamp': 'detail',
    'hull': 'body',
    'light': 'detail',
    'metal': 'trim',
    'porch': 'trim',
    'tail_lamp': 'detail',
    'tower': 'wall',
    'wood': 'detail',
}

# Per-colour sub-roles of a role (skos:broader, named '<role>:<suffix>'): a striped wall is wall:stripe-a, wall:stripe-b...
SUBROLES = {
    'body:stripe-a': 'body',
    'body:stripe-b': 'body',
    'body:stripe-c': 'body',
    'roof:stripe-a': 'roof',
    'roof:stripe-b': 'roof',
    'roof:stripe-c': 'roof',
    'wall:stripe-a': 'wall',
    'wall:stripe-b': 'wall',
    'wall:stripe-c': 'wall',
}

# What a person says (skos:altLabel) -> role, used by role_edits.
WORDS = {
    'base': 'base',
    'baseplate': 'base',
    'body': 'body',
    'bodywork': 'body',
    'brickwork': 'wall',
    'canopy': 'glass',
    'chassis': 'chassis',
    'cornice': 'trim',
    'detail': 'detail',
    'details': 'detail',
    'door': 'door',
    'doors': 'door',
    'engine': 'engine',
    'engines': 'engine',
    'floor': 'base',
    'foundation': 'base',
    'frame': 'chassis',
    'glass': 'glass',
    'glazing': 'glass',
    'ground': 'base',
    'masonry': 'wall',
    'panes': 'glass',
    'plinth': 'trim',
    'rim': 'wheel',
    'rims': 'wheel',
    'roof': 'roof',
    'roofing': 'roof',
    'roofs': 'roof',
    'tire': 'tyre',
    'tires': 'tyre',
    'tower': 'tower',
    'towers': 'tower',
    'trim': 'trim',
    'turret': 'tower',
    'turrets': 'tower',
    'tyre': 'tyre',
    'tyres': 'tyre',
    'wall': 'wall',
    'walls': 'wall',
    'wheel': 'wheel',
    'wheels': 'wheel',
    'window': 'window',
    'windows': 'window',
    'windscreen': 'glass',
    'windshield': 'glass',
    'wing': 'wing',
    'wings': 'wing',
}

# The grammar's phrasing of its roles (skos:hiddenLabel), feature words included.
GRAMMAR_WORDS = {
    'roof': ('roof', 'roofing', 'slates', 'thatch', 'tiles'),
    'wall': ('bricks', 'brickwork', 'masonry', 'stonework', 'wall', 'walls'),
    'window': ('window', 'window frame', 'window frames', 'windows'),
    'door': ('door', 'doors', 'entrance', 'gate', 'gates'),
    'glass': ('glass', 'glazing', 'panes'),
    'base': ('base', 'baseplate', 'grass', 'ground', 'lawn', 'path', 'pavement'),
    'tower': ('tower', 'towers', 'turret', 'turrets'),
    'detail': ('balcony', 'chimney', 'detail', 'details', 'porch', 'sign'),
    'trim': ('band', 'bands', 'cornice', 'pillars', 'plinth', 'quoins', 'trim'),
    'structure': ('floor', 'floors', 'structure'),
}
# END GENERATED


def canonical(role):
    """The broader role a role name stands for ('tower' -> 'wall', 'hull' -> 'body', 'wall:stripe-a' -> 'wall'); any other name is its own."""
    return ALIAS.get(role) or SUBROLES.get(role) or role


def subroles_of(role):
    """The per-colour sub-roles of a role in their fixed order ('wall' -> ('wall:stripe-a', 'wall:stripe-b', 'wall:stripe-c')); () for none."""
    return tuple(s for s, parent in sorted(SUBROLES.items()) if parent == role)


def is_role(name):
    """True for a role name or a sub-role name of the vocabulary."""
    return name in ROLES or name in SUBROLES


def role_for_word(word):
    """Role for a word from the grammar's phrasing ('tiles' -> 'roof'), or None."""
    word = word.strip().casefold()
    for role, words in GRAMMAR_WORDS.items():
        if word in words:
            return role
    return None
