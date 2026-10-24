"""Parametric house: a reserved-opening shell, a gable or flat roof, and optional porch, chimney and dormer."""
from __future__ import annotations

import math

from ..architecture import Module, attach_roof, flat_roof, gable, porch, room
from .base import Generator, colour_param, tag_roles
from .colours import colour_code

SIDES = ['front', 'back', 'left', 'right']
DOOR_SIDES = ['left', 'centre', 'right']
DOOR_STYLES = ['traditional', 'glazed', 'hatch']


def even(value, low, high, name, notes):
    snapped = min(high, max(low, value + value % 2))
    if snapped != value:
        notes.append(f'{name} {value} adjusted to {snapped} (even, {low}-{high})')
    return snapped


def door_centre(width, side):
    """Door x in studs. Left/right doors leave a one-stud pier at the corner when the wall allows it."""
    edge = width // 2 - 2 - (1 if width >= 10 else 0)
    return {'left': -edge, 'right': edge, 'centre': 0}[side]


def window_slots(width, door_x):
    """Integer centres of 4-wide front windows that avoid the 4-wide door, spread evenly per free span."""
    spans = [(-width // 2, door_x - 2), (door_x + 2, width // 2)] if door_x is not None else [(-width // 2, width // 2)]
    slots = []
    for low, high in spans:
        n = (high - low) // 4
        if n <= 0:
            continue
        gap = ((high - low) - 4 * n) / (n + 1)
        slots += [math.floor(low + gap * (k + 1) + 4 * k + 2 + 0.5) for k in range(n)]
    return sorted(slots, key=lambda x: (abs(x - (door_x or 0)), x))


def build(p):
    notes = []
    width = even(p['width'], 6, 24, 'width', notes)
    depth = even(p['depth'], 6, 20, 'depth', notes)
    courses = p['courses']
    body, trim, roof = colour_code(p['wall_colour']), colour_code(p['trim_colour']), colour_code(p['roof_colour'])
    door_colour, glass = colour_code(p['door_colour']), colour_code(p['glass_colour'], glass=True)
    has_door = p['door_side'] != 'none'
    if has_door and courses < 6:
        courses = 6
        notes.append('courses raised to 6: the door is six courses tall')
    if width < 8 and p['door_side'] in ('left', 'right'):
        door_x = 0
        notes.append('door centred: the wall is too narrow to offset it')
    else:
        door_x = door_centre(width, p['door_side']) if has_door else None

    # Windows: front slots first (nearest the door), then the other requested sides, one 4-wide window each.
    wanted, sides = p['windows'], list(p['window_sides'])
    front = []
    if 'front' in sides:
        front = window_slots(width, door_x)[:wanted]
    placed = {'front': len(front)}
    left_over = wanted - len(front)
    side_windows, back_window = [], False
    for side in ('left', 'right', 'back'):
        if left_over <= 0 or side not in sides:
            continue
        fits = depth - 2 >= 4 if side in ('left', 'right') else width >= 6
        if fits:
            left_over -= 1
            placed[side] = 1
            if side == 'back':
                back_window = True
            else:
                side_windows.append(side)
    if left_over > 0:
        notes.append(f'{wanted - left_over} of {wanted} windows fit on the requested sides')

    shell = room('house-shell', width, depth, body=body, trim=trim, door=door_x, windows=tuple(front), rows=courses,
                 glass=glass, door_style=p['door_style'], door_colour=door_colour, sides=False, material=None,
                 side_windows=side_windows, back_window=back_window)
    tiers_max = depth // 2 - 1
    tiers = min(p['roof_tiers'], tiers_max) if p['roof_tiers'] else tiers_max
    if p['roof_tiers'] and tiers != p['roof_tiers']:
        notes.append(f'roof_tiers {p["roof_tiers"]} limited to {tiers} by the depth')
    dormer = p['dormer'] and p['roof_style'] == 'gable' and width >= 8 and tiers >= 3
    if p['dormer'] and not dormer:
        notes.append('dormer skipped: needs a gable roof, width >= 8 and at least 3 roof tiers')
    chimney = None
    if p['chimney'] != 'none' and p['roof_style'] == 'gable':
        chimney = ((-1 if p['chimney'] == 'left' else 1) * max(1, width // 4), colour_code(p['chimney_colour']))
    elif p['chimney'] != 'none':
        notes.append('chimney skipped: needs a gable roof')
    if p['roof_style'] == 'gable':
        top = gable('house-roof', width + 2, depth, roof=roof, gable_colour=body, tiers=tiers, dormer=dormer,
                    chimney=(chimney[0], chimney[1]) if chimney else None)
    else:
        top = flat_roof('house-roof', width, depth, trim, roof)

    scene = Module('house', f'House {width} x {depth} studs, {courses} courses, front -Z')
    attach_roof(scene, shell, top, id='building', colour=body)
    if p['porch'] and has_door:
        pw = 8
        scene.add(porch('porch', trim=trim, roof=roof, w=pw), trim, door_x, 0, -depth / 2 - 2, id='entry-porch',
                  purpose='Rear edge abuts the front wall; the four-stud entry stays clear')
    elif p['porch']:
        notes.append('porch skipped: it needs a door')
    plan = scene.plan()
    plan['author'] = 'ldraw-nova generators'
    tag_roles(plan, [
        ('house-shell', {body: 'wall', trim: 'trim', 70: 'base'}),
        ('window4', {glass: 'window', trim: 'trim'}),
        ('door4', {door_colour: 'door', trim: 'trim'}),
        ('house-roof', lambda q: 'chimney' if q['id'].startswith('chimney') else {roof: 'roof', body: 'wall', trim: 'trim'}.get(q['colour'])),
        ('porch', {trim: 'porch', roof: 'roof'}),
    ])
    return plan, notes


GENERATOR = Generator(
    id='house', title='House', families=('house', 'building'),
    kinds=('house', 'cottage', 'home', 'bungalow', 'villa', 'barn', 'cabin', 'shop', 'townhouse'),
    description='Brick house: shell with door and windows, gabled or flat roof, optional porch, chimney, dormer.',
    params={
        'width': {'type': 'integer', 'minimum': 6, 'maximum': 24, 'x-even': True, 'default': 8,
                  'description': 'Front wall width in studs (even; odd values round up)'},
        'depth': {'type': 'integer', 'minimum': 6, 'maximum': 20, 'x-even': True, 'default': 6,
                  'description': 'Side wall length in studs (even)'},
        'courses': {'type': 'integer', 'minimum': 4, 'maximum': 14, 'default': 6,
                    'description': 'Wall height in brick courses; a door needs at least 6, 10+ adds an upper window row'},
        'door_side': {'enum': DOOR_SIDES + ['none'], 'default': 'left', 'description': 'Where the door sits on the front'},
        'door_style': {'enum': DOOR_STYLES, 'default': 'traditional'},
        'windows': {'type': 'integer', 'minimum': 0, 'maximum': 8, 'default': 2,
                    'description': 'Total 4-stud windows, placed on window_sides in front, left, right, back order'},
        'window_sides': {'type': 'array', 'uniqueItems': True, 'items': {'enum': SIDES},
                         'default': ['front', 'left', 'right', 'back']},
        'roof_style': {'enum': ['gable', 'flat'], 'default': 'gable'},
        'roof_tiers': {'type': 'integer', 'minimum': 0, 'maximum': 9, 'default': 0,
                       'description': 'Slope courses of a gable roof; 0 = as steep as the depth allows'},
        'porch': {'type': 'boolean', 'default': False, 'description': '8x4 pillared porch over the door'},
        'chimney': {'enum': ['none', 'left', 'right'], 'default': 'none', 'description': 'Chimney on the gable ridge'},
        'dormer': {'type': 'boolean', 'default': False, 'description': 'Dormer window in the front roof slope'},
        'wall_colour': colour_param('Walls') | {'default': 'tan'},
        'trim_colour': colour_param('Window frames, door frame, eaves ring') | {'default': 'white'},
        'roof_colour': colour_param('Roof') | {'default': 'dark red'},
        'door_colour': colour_param('Door leaf') | {'default': 'reddish brown'},
        'glass_colour': colour_param('Window glass') | {'default': 'clear'},
        'chimney_colour': colour_param('Chimney bricks') | {'default': 'dark grey'},
    },
    roles=('wall', 'trim', 'roof', 'door', 'window', 'porch', 'chimney', 'base'),
    build=build,
    coverage=('door', 'windows', 'roof tiers', 'porch', 'chimney', 'dormer', 'two storeys'),
    quality='Compact stepped-roof house; windows are 4 studs wide, so a front fits door plus (width-4)//4 windows.')
