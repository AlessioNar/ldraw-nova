"""Scenery generators: garden accents that go beside a building (a tree whose trunk height and colours can be set)."""
from __future__ import annotations

from ..architecture import Module, slab
from .base import Generator, colour_param
from .colours import colour_code

TRUNK_BRICK_H = 24     # one round 1x1 brick
CANOPY_ABOVE_TRUNK = 8  # first foliage layer sits one plate above the top trunk brick


def build(p):
    """Same stacking as architecture.tree (plinth, round-brick trunk, four alternating foliage layers) with a variable trunk."""
    courses = p['trunk_courses']
    leaves, leaves2, trunk, flower, ground = (colour_code(p['foliage_colour']), colour_code(p['foliage_accent']),
                                              colour_code(p['trunk_colour']), colour_code(p['flower_colour']),
                                              colour_code(p['ground_colour']))
    m = Module('garden-tree', f'Garden tree, {courses}-course trunk; bottom Y=0; foliage needs a nine-stud clearing')
    m.role = 'base'
    slab(m, -2, -2, 4, 4, 8, ground)
    m.role = 'trunk'
    slab(m, -1, -1, 2, 2, 16, trunk)
    for i in range(courses):
        m.add('3062b', trunk, -.5, 40 + TRUNK_BRICK_H * i, -.5)
    top = 40 + TRUNK_BRICK_H * (courses - 1) + CANOPY_ABOVE_TRUNK     # brick h is its top face, so this is one plate above the last brick
    for i, (ref, yaw) in enumerate([('2417', 0), ('2417', 90), ('2423', 180), ('2423', 0)]):
        m.add(ref, leaves if i % 2 == 0 else leaves2, -.5, top + 32 * i, -.5, yaw=yaw, role='foliage')
        if i < 3:
            m.add('3062b', trunk, -.5, top + 24 + 32 * i, -.5)
    for x in [-1.5, .5, 1.5]:
        m.add('24866', flower, x, 16, -1.5, role='flower')
    m.anchor('trunk', -.5, top - 8, -.5)
    plan = m.plan()
    plan['author'] = 'ldraw-nova generators'
    return plan, []


GENERATOR = Generator(
    id='tree', title='Tree', families=('garden', 'scene'), kinds=('tree', 'oak', 'oak tree', 'pine', 'pine tree', 'fir', 'bush', 'shrub', 'garden tree'),
    description='Garden tree on a 4x4 plinth: round-brick trunk of adjustable height under four layers of foliage.',
    params={
        'trunk_courses': {'type': 'integer', 'minimum': 3, 'maximum': 10, 'default': 5,
                          'description': 'Trunk height in brick courses (3 is a shrub, 10 a tall tree)'},
        'foliage_colour': colour_param('Leaves') | {'default': 'dark green'},
        'foliage_accent': colour_param('Second foliage layer') | {'default': 'green'},
        'trunk_colour': colour_param('Trunk') | {'default': 'reddish brown'},
        'flower_colour': colour_param('Flowers at the foot') | {'default': 'dark pink'},
        'ground_colour': colour_param('Plinth') | {'default': 'tan'},
    },
    roles=('base', 'trunk', 'foliage', 'flower'), build=build, coverage=('trunk height', 'colours'),
    quality='Small accent piece for a scene: one stylised tree')
