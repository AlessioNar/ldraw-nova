"""Generator wrappers for the reference recipes (arcade bay, street lantern)."""
from __future__ import annotations

from ..reference_recipes import RECIPES, recipe_plan
from .base import Generator, colour_param, tag_roles
from .colours import colour_code

INFO = {'arcade-bay': (('building', 'scene'), ('arcade', 'arch', 'archway', 'colonnade', 'bay', 'gateway')),
        'street-lantern': (('garden', 'scene'), ('lamp', 'lantern', 'street lamp', 'lamppost', 'streetlight'))}


def make(name):
    recipe = RECIPES[name]

    def build(p):
        colour, accent = colour_code(p['colour']), colour_code(p['accent'])
        plan = recipe_plan(name, height=p['height'], colour=colour, accent=accent)

        def role(q):
            if q['colour'] == 46:
                return 'light'
            return 'accent' if q['colour'] == accent and colour != accent else 'body'
        tag_roles(plan, [('recipe-', role)])
        return plan, []

    families, kinds = INFO[name]
    return Generator(
        id=name, title=name.replace('-', ' ').capitalize(), families=families, kinds=kinds,
        description=recipe['description'],
        params={'height': {'type': 'integer', 'minimum': 2, 'maximum': 10, 'default': recipe['default_height'],
                           'description': 'Brick courses of the piers or post'},
                'colour': colour_param('Main colour') | {'default': 'tan' if name == 'arcade-bay' else 'black'},
                'accent': colour_param('Accent colour') | {'default': 'dark tan' if name == 'arcade-bay' else 'light grey'}},
        roles=('body', 'accent', 'light'), build=build, coverage=('height', 'colours'),
        quality='Small accent piece for a scene, not a whole building')


GENERATORS = [make(name) for name in RECIPES]
