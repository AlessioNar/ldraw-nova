"""Furniture generators: stool, chair, dining table, bed, sofa and bookshelf, in the parts the reference sets use for them
(see ldraw_tools/furniture.py and tools/harvest_furniture.py). Every piece stands on Y=0, is centred on x and z and has its front at -Z."""
from __future__ import annotations

from ..architecture import Module, line, slab
from .base import Generator, colour_param
from .colours import colour_code

ROUND_BRICK, ROUND_BRICK_2X2, ROUND_PLATE_2X2, BRICK_1X1, BRICK_1X2, PLATE_2X2 = '3062b', '3941', '4032a', '3005', '3004', '3022'


def finish(m):
    plan = m.plan()
    plan['author'] = 'ldraw-nova generators'
    return plan, []


def deck(m, x0, z0, w, d, h, colour):
    """A 16-LDU top that is one piece: plates in rows along x under plates in columns along z, with every other run starting on a
    shorter plate so that no joint lines up with its neighbour's."""
    for j in range(d):
        line(m, x0, z0 + j + .5, w, h - 8, colour, plate=True, bond=j % 2 == 1 and w > 2)
    for i in range(w):
        line(m, x0 + i + .5, z0, d, h, colour, axis='z', plate=True, bond=i % 2 == 1 and d > 2)


# --- Stool and chair -----------------------------------------------------------------------------------------------------

def build_stool(p):
    n = p['courses']
    seat, leg = colour_code(p['seat_colour']), colour_code(p['leg_colour'])
    m = Module('stool', f'Round stool, {n} course{"" if n == 1 else "s"} high; bottom Y=0; two studs across')
    m.role = 'leg'
    for i in range(n):
        m.add(ROUND_BRICK_2X2, leg, 0, 24 * (i + 1), 0)
    m.role = 'seat'
    m.add(ROUND_PLATE_2X2, seat, 0, 24 * n + 8, 0)
    return finish(m)


def build_chair(p):
    n = p['back_courses']
    seat, frame = colour_code(p['seat_colour']), colour_code(p['frame_colour'])
    m = Module('chair', f'Chair with a {n}-course back; bottom Y=0; front -Z; sits on a 2x2 seat')
    m.role = 'leg'
    for x in (-.5, .5):
        for z in (-.5, .5):
            m.add(ROUND_BRICK, frame, x, 24, z)
    m.role = 'seat'
    m.add(PLATE_2X2, seat, 0, 32, 0)
    m.role = 'back'
    for i in range(n):
        m.add(BRICK_1X2, frame, 0, 32 + 24 * (i + 1), .5)
    return finish(m)


STOOL = Generator(
    id='stool', title='Stool', families=('furniture',), kinds=('stool', 'footstool'),
    description='Round stool: a stack of round 2x2 bricks under a round plate seat.',
    params={
        'courses': {'type': 'integer', 'minimum': 1, 'maximum': 4, 'default': 2, 'description': 'Height in round-brick courses (1 is a footstool, 4 a bar stool)'},
        'seat_colour': colour_param('Seat') | {'default': 'red'},
        'leg_colour': colour_param('Stem') | {'default': 'dark bluish grey'},
    },
    roles=('leg', 'seat'), build=build_stool, coverage=('height', 'colours'), quality='Reads as a stool at minifigure scale')

CHAIR = Generator(
    id='chair', title='Chair', families=('furniture',), kinds=('chair', 'armchair'),
    description='Chair on four round-brick legs with a 2x2 seat and a brick back.',
    params={
        'back_courses': {'type': 'integer', 'minimum': 1, 'maximum': 4, 'default': 2, 'description': 'Height of the back in brick courses'},
        'seat_colour': colour_param('Seat') | {'default': 'red'},
        'frame_colour': colour_param('Legs and back') | {'default': 'reddish brown'},
    },
    roles=('leg', 'seat', 'back'), build=build_chair, coverage=('back height', 'colours'), quality='Reads as a chair at minifigure scale')

# --- Table ---------------------------------------------------------------------------------------------------------------


def build_table(p):
    w, d, n = p['width'], p['depth'], p['leg_courses']
    top, leg = colour_code(p['top_colour']), colour_code(p['leg_colour'])
    m = Module('dining-table', f'Table {w}x{d} studs on {n}-course legs; bottom Y=0; front -Z')
    x0, z0 = -w / 2, -d / 2
    m.role = 'leg'
    for x in (x0 + .5, x0 + w - .5):
        for z in (z0 + .5, z0 + d - .5):
            for i in range(n):
                m.add(ROUND_BRICK, leg, x, 24 * (i + 1), z)
    m.role = 'top'
    deck(m, x0, z0, w, d, 24 * n + 16, top)
    return finish(m)


TABLE = Generator(
    id='dining-table', title='Table', families=('furniture',), kinds=('table', 'desk'),
    description='Table or desk: a two-layer plate top on four round-brick legs.',
    params={
        'width': {'type': 'integer', 'minimum': 2, 'maximum': 8, 'default': 6, 'description': 'Top width in studs'},
        'depth': {'type': 'integer', 'minimum': 2, 'maximum': 6, 'default': 4, 'description': 'Top depth in studs'},
        'leg_courses': {'type': 'integer', 'minimum': 1, 'maximum': 3, 'default': 2, 'description': 'Leg height in brick courses (1 is a coffee table)'},
        'top_colour': colour_param('Tabletop') | {'default': 'reddish brown'},
        'leg_colour': colour_param('Legs') | {'default': 'dark brown'},
    },
    roles=('leg', 'top'), build=build_table, coverage=('width', 'depth', 'height', 'colours'), quality='Reads as a table or desk at minifigure scale')

# --- Bed -----------------------------------------------------------------------------------------------------------------


def build_bed(p):
    w, length = p['width'], p['length']
    frame, mattress, pillow = (colour_code(p[k]) for k in ('frame_colour', 'mattress_colour', 'pillow_colour'))
    m = Module('bed', f'Bed {w}x{length} studs; bottom Y=0; head at +Z, foot at -Z')
    x0, z0 = -w / 2, -length / 2
    m.role = 'leg'
    for x in (x0 + .5, x0 + w - .5):
        for z in (z0 + .5, z0 + length - .5):
            m.add(BRICK_1X1, frame, x, 24, z)
    m.role = 'frame'
    deck(m, x0, z0, w, length, 40, frame)
    m.role = 'headboard'
    for i in (1, 2):
        line(m, x0, z0 + length - .5, w, 40 + 24 * i, frame)
    line(m, x0, z0 + .5, w, 64, frame)                                  # a lower board at the foot
    m.role = 'mattress'
    slab(m, x0, z0 + 1, w, length - 2, 48, mattress)
    m.role = 'pillow'
    slab(m, x0 + 1, z0 + length - 3, w - 2, 2, 56, pillow)
    return finish(m)


BED = Generator(
    id='bed', title='Bed', families=('furniture',), kinds=('bed', 'bunk', 'cot'),
    description='Bed on four posts: a plate base with a head and foot board, a mattress and a pillow.',
    params={
        'width': {'type': 'integer', 'minimum': 3, 'maximum': 6, 'default': 4, 'description': 'Width in studs (3-4 single, 5-6 double)'},
        'length': {'type': 'integer', 'minimum': 6, 'maximum': 10, 'default': 8, 'description': 'Length in studs'},
        'frame_colour': colour_param('Frame and boards') | {'default': 'reddish brown'},
        'mattress_colour': colour_param('Mattress') | {'default': 'light bluish grey'},
        'pillow_colour': colour_param('Pillow') | {'default': 'white'},
    },
    roles=('leg', 'frame', 'headboard', 'mattress', 'pillow'), build=build_bed, coverage=('width', 'length', 'colours'),
    quality='Reads as a bed at minifigure scale')

# --- Sofa ----------------------------------------------------------------------------------------------------------------


def build_sofa(p):
    w, d = p['width'], p['depth']
    frame, cushion = colour_code(p['frame_colour']), colour_code(p['cushion_colour'])
    m = Module('sofa', f'Sofa {w}x{d} studs; bottom Y=0; front -Z, back at +Z')
    x0, z0 = -w / 2, -d / 2
    m.role = 'frame'
    deck(m, x0, z0, w, d, 16, frame)
    for x in (x0 + .5, x0 + w - .5):                                  # arms
        line(m, x, z0, d - 1, 40, frame, axis='z')
    m.role = 'back'
    for h in (40, 64):
        line(m, x0, z0 + d - .5, w, h, frame)
    m.role = 'cushion'
    slab(m, x0 + 1, z0, w - 2, d - 1, 24, cushion)
    return finish(m)


SOFA = Generator(
    id='sofa', title='Sofa', families=('furniture',), kinds=('sofa', 'couch', 'settee', 'loveseat', 'bench'),
    description='Sofa: a plate base with arms, a two-course back and a cushion seat.',
    params={
        'width': {'type': 'integer', 'minimum': 4, 'maximum': 10, 'default': 8, 'description': 'Width in studs (4-5 a loveseat)'},
        'depth': {'type': 'integer', 'minimum': 3, 'maximum': 5, 'default': 4, 'description': 'Depth in studs, back included'},
        'frame_colour': colour_param('Base, arms and back') | {'default': 'dark bluish grey'},
        'cushion_colour': colour_param('Seat cushion') | {'default': 'blue'},
    },
    roles=('frame', 'back', 'cushion'), build=build_sofa, coverage=('width', 'depth', 'colours'), quality='Reads as a sofa at minifigure scale')

# --- Bookshelf -----------------------------------------------------------------------------------------------------------

COMPARTMENT = 48                    # clear height between boards: two brick courses
BOARD = 8


def build_shelf(p):
    w, n = p['width'], p['shelves']
    frame, book = colour_code(p['frame_colour']), colour_code(p['book_colour'])
    spare = colour_code('light bluish grey')
    m = Module('bookshelf', f'Bookshelf {w} studs wide with {n} compartment{"" if n == 1 else "s"}, two studs deep; bottom Y=0; open side -Z')
    x0 = -w / 2
    for i in range(n + 1):
        top = BOARD + i * (COMPARTMENT + BOARD)
        m.role = 'board'
        slab(m, x0, -1, w, 2, top, frame)
        if i == n:
            break
        m.role = 'upright'
        for x in (x0 + .5, x0 + w - .5):
            for c in (1, 2):
                m.add(BRICK_1X2, frame, x, top + 24 * c, 0, yaw=90)
        if p['books']:
            m.role = 'book'
            for k in range(w - 2):
                m.add(BRICK_1X1, book if k % 2 == 0 else spare, x0 + 1.5 + k, top + 24, -.5)
    return finish(m)


SHELF = Generator(
    id='bookshelf', title='Bookshelf', families=('furniture',), kinds=('bookshelf', 'bookcase', 'shelf', 'shelves', 'cabinet', 'cupboard'),
    description='Open shelving: boards between brick uprights, with an optional row of books on each shelf.',
    params={
        'width': {'type': 'integer', 'minimum': 3, 'maximum': 8, 'default': 4, 'description': 'Width in studs'},
        'shelves': {'type': 'integer', 'minimum': 1, 'maximum': 4, 'default': 3, 'description': 'Compartments, one above the other'},
        'books': {'type': 'boolean', 'default': True, 'description': 'A row of books on every shelf'},
        'frame_colour': colour_param('Boards and uprights') | {'default': 'reddish brown'},
        'book_colour': colour_param('Books') | {'default': 'dark red'},
    },
    roles=('board', 'upright', 'book'), build=build_shelf, coverage=('width', 'shelves', 'books', 'colours'),
    quality='Reads as a bookshelf at minifigure scale')

GENERATORS = [STOOL, CHAIR, TABLE, BED, SOFA, SHELF]
