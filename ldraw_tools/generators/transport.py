"""Train and ship generators: a steam-style train on a length of track, and a container ship with a stepped bow."""
from __future__ import annotations

from ..architecture import WIDE_PLATES, Module, lengths, line, slab
from .base import Generator, colour_param
from .colours import colour_code

ROUND_BRICK, PLATE_1X1, ROUND_BRICK_2X2, BRICK_2X4 = '3062b', '3024', '3941', '3001'
BRICKS_2 = {8: '3007', 6: '2456', 4: '3001', 3: '3002', 2: '3003'}      # two-wide bricks by length

# --- Train -------------------------------------------------------------------------------------------------------------

LOCO_LENGTH, WAGON_LENGTH, TRAIN_WIDTH = 14, 10, 6
BALLAST_TOP, RAIL_TOP, WHEEL_TOP, CHASSIS_TOP = 8, 16, 40, 48     # plates on the ballast, round brick wheels on the rails, chassis plates on the wheels


def build_train(p):
    """Locomotive (boiler, chimney, cab) and open wagons on one chassis plate that rests on round-brick wheels over two rails."""
    wagons = p['wagons']
    loco, wagon, roof, wheel, ballast = (colour_code(p[k]) for k in ('loco_colour', 'wagon_colour', 'roof_colour', 'wheel_colour', 'track_colour'))
    rail, lamp = colour_code('light bluish grey'), colour_code('yellow')
    total = LOCO_LENGTH + WAGON_LENGTH * wagons
    m = Module('toy-train', f'Steam-style train: locomotive and {wagons} wagon{"" if wagons == 1 else "s"}, {total} studs long; bottom Y=0; front at -Z')
    m.role = 'base'
    slab(m, -3, 0, TRAIN_WIDTH, total, BALLAST_TOP, ballast)
    m.role = 'rail'
    line(m, -2.5, 0, total, RAIL_TOP, rail, axis='z', plate=True)
    line(m, 2.5, 0, total, RAIL_TOP, rail, axis='z', plate=True, bond=True)      # staggered joints lock the ballast plates together
    m.role = 'wheel'
    for start, length in [(0, LOCO_LENGTH), *((LOCO_LENGTH + WAGON_LENGTH * i, WAGON_LENGTH) for i in range(wagons))]:
        for z in (1.5, 3.5, length - 3.5, length - 1.5):
            for x in (-2.5, 2.5):
                m.add(ROUND_BRICK, wheel, x, WHEEL_TOP, start + z)
    m.role = 'chassis'
    slab(m, -3, 0, TRAIN_WIDTH, total, CHASSIS_TOP, wheel)
    # Locomotive: a four-stud boiler up front, a six-stud cab behind it
    m.role = 'body'
    for course in range(3):
        top = CHASSIS_TOP + 24 * (course + 1)
        for x in (-1.5, -.5, .5, 1.5):
            line(m, x, 0, 8, top, loco, axis='z')
        line(m, -3, 8.5, TRAIN_WIDTH, top, loco)
        line(m, -3, 13.5, TRAIN_WIDTH, top, loco)
        for x in (-2.5, 2.5):
            line(m, x, 9, 4, top, loco, axis='z')
    m.role = 'roof'
    slab(m, -3, 8, TRAIN_WIDTH, 6, CHASSIS_TOP + 72 + 8, roof)
    m.role = 'chimney'
    m.add(ROUND_BRICK, roof, -.5, CHASSIS_TOP + 96, 1.5)
    m.add(PLATE_1X1, wheel, -.5, CHASSIS_TOP + 104, 1.5)
    m.role = 'lamp'
    m.add(PLATE_1X1, lamp, .5, CHASSIS_TOP + 80, .5)
    # Wagons: low open boxes with a block of cargo
    for i in range(wagons):
        z0 = LOCO_LENGTH + WAGON_LENGTH * i
        m.role = 'body'
        for course in range(2):
            top = CHASSIS_TOP + 24 * (course + 1)
            for x in (-2.5, 2.5):
                line(m, x, z0, WAGON_LENGTH, top, wagon, axis='z')
            for z in (z0 + .5, z0 + WAGON_LENGTH - .5):
                line(m, -2, z, 4, top, wagon)
        m.role = 'cargo'
        m.add(BRICK_2X4, roof, 0, CHASSIS_TOP + 24, z0 + 5, yaw=90)
    plan = m.plan()
    plan['author'] = 'ldraw-nova generators'
    return plan, []


TRAIN = Generator(
    id='toy-train', title='Train', families=('train', 'vehicle'),
    kinds=('train', 'steam train', 'locomotive', 'steam engine', 'goods train', 'freight train', 'railway', 'tram'),
    description='Steam-style locomotive with a chimney and cab, pulling open wagons with cargo along a stretch of track.',
    params={
        'wagons': {'type': 'integer', 'minimum': 0, 'maximum': 6, 'default': 2, 'description': 'Open wagons behind the locomotive'},
        'loco_colour': colour_param('Locomotive boiler and cab') | {'default': 'red'},
        'wagon_colour': colour_param('Wagon sides') | {'default': 'blue'},
        'roof_colour': colour_param('Cab roof, chimney and cargo') | {'default': 'dark bluish grey'},
        'wheel_colour': colour_param('Wheels and chassis') | {'default': 'black'},
        'track_colour': colour_param('Track bed') | {'default': 'tan'},
    },
    roles=('base', 'rail', 'wheel', 'chassis', 'body', 'roof', 'chimney', 'lamp', 'cargo'), build=build_train,
    coverage=('wagon count', 'colours'), quality='Recognisable toy train; wheels are stacked round bricks, not flanged train wheels')

# --- Ship --------------------------------------------------------------------------------------------------------------

BOW = (2, 4, 6)                  # widths of the three bow rows, tip first; the hull is eight studs wide behind them
HULL_WIDTH, HULL_FLOOR = 8, 8
BAY_LENGTH, CABIN_LENGTH = 4, 4
DECK_TOP = HULL_FLOOR + 24 * 3 + 8
MIN_LENGTH, MAX_LENGTH = 16, 40


def max_bays(length):
    """Container bays that fit between the bow and the deckhouse (one stud clear at each end)."""
    return max(0, (length - len(BOW) - 1 - CABIN_LENGTH - 2 - 1) // BAY_LENGTH)


def build_ship(p):
    """Stepped-bow hull of three brick courses under a plate deck, a deckhouse and funnel aft, and bays of stacked containers."""
    length = p['length'] + p['length'] % 2
    notes = []
    if length != p['length']:
        notes.append(f'length adjusted to {length} (even)')
    bays = min(p['bays'], max_bays(length))
    if bays != p['bays']:
        notes.append(f'bays adjusted to {bays} (what fits a {length}-stud hull)')
    hull, deck, cabin, funnel, stripe, box, box2 = (colour_code(p[k]) for k in (
        'hull_colour', 'deck_colour', 'cabin_colour', 'funnel_colour', 'stripe_colour', 'cargo_colour', 'cargo_accent'))
    m = Module('cargo-ship', f'Container ship, {length} studs long with {bays} bay{"" if bays == 1 else "s"}; bottom Y=0; bow at -Z')
    rows = [*BOW, *[HULL_WIDTH] * (length - len(BOW))]

    def layer(h, colour):
        """A plate layer. A two-stud strip down the whole length locks the one-stud bow rows; behind the bow, two-stud plates
        offset by one stud from the brick lanes lock those to each other."""
        z = 0
        for n in lengths(length):
            m.add(WIDE_PLATES[n], colour, 0, h, z + n / 2, yaw=90 if n > 1 else 0)
            z += n
        for z, w in enumerate(rows[:len(BOW)]):
            if w > 2:
                line(m, -w / 2, z + .5, w / 2 - 1, h, colour, plate=True)
                line(m, 1, z + .5, w / 2 - 1, h, colour, plate=True)
        z = len(BOW)
        for n in lengths(length - z):
            for x in (-2, 2):
                m.add(WIDE_PLATES[n], colour, x, h, z + n / 2, yaw=90 if n > 1 else 0)
            z += n
        for x in (-3.5, 3.5):
            line(m, x, len(BOW), length - len(BOW), h, colour, axis='z', plate=True)

    m.role = 'hull'
    layer(HULL_FLOOR, hull)                            # floor, then three brick courses: a full row at the bow bulkhead, four lanes behind it
    for course in range(3):
        top = HULL_FLOOR + 24 * (course + 1)
        colour = stripe if course == 0 else hull
        for z, w in enumerate(rows[:len(BOW) + 1]):
            line(m, -w / 2, z + .5, w, top, colour)
        z = len(BOW) + 1
        for n in lengths(length - z, 8):
            for x in (-3, -1, 1, 3):
                m.add(BRICKS_2[n], colour, x, top, z + n / 2, yaw=90)
            z += n
    m.role = 'deck'
    layer(DECK_TOP, deck)
    m.role = 'cabin'                                    # deckhouse, three courses, set in from the stern
    z0 = length - 2 - CABIN_LENGTH
    for course in range(3):
        top = DECK_TOP + 24 * (course + 1)
        for z in (z0 + .5, z0 + CABIN_LENGTH - .5):
            line(m, -2, z, 4, top, cabin)
        for x in (-1.5, 1.5):
            line(m, x, z0 + 1, CABIN_LENGTH - 2, top, cabin, axis='z')
    slab(m, -2, z0, 4, CABIN_LENGTH, DECK_TOP + 72 + 8, stripe)
    m.role = 'funnel'
    for i in range(2):
        m.add(ROUND_BRICK_2X2, funnel, 0, DECK_TOP + 24 * (i + 1), length - 1)
    m.role = 'cargo'
    for bay in range(bays):
        z = len(BOW) + 1 + BAY_LENGTH * bay + BAY_LENGTH / 2
        for x in (-2, 2):
            for level, colour in enumerate((box if bay % 2 == 0 else box2, box2 if bay % 2 == 0 else box)):
                m.add(BRICK_2X4, colour, x, DECK_TOP + 24 * (level + 1), z, yaw=90)
    plan = m.plan()
    plan['author'] = 'ldraw-nova generators'
    return plan, notes


SHIP = Generator(
    id='cargo-ship', title='Cargo ship', families=('boat', 'ship'),
    kinds=('ship', 'cargo ship', 'container ship', 'steamer', 'steamship', 'tanker', 'liner', 'ferry'),
    description='Seagoing ship with a stepped bow, a deckhouse and funnel aft and bays of stacked containers on deck.',
    params={
        'length': {'type': 'integer', 'minimum': MIN_LENGTH, 'maximum': MAX_LENGTH, 'x-even': True, 'default': 24,
                   'description': 'Hull length in studs (even)'},
        'bays': {'type': 'integer', 'minimum': 0, 'maximum': 7, 'default': 3,
                 'description': 'Container bays on deck (fewer are built if the hull is too short)'},
        'hull_colour': colour_param('Hull') | {'default': 'dark blue'},
        'stripe_colour': colour_param('Waterline stripe and deckhouse roof') | {'default': 'red'},
        'deck_colour': colour_param('Deck') | {'default': 'dark bluish grey'},
        'cabin_colour': colour_param('Deckhouse') | {'default': 'white'},
        'funnel_colour': colour_param('Funnel') | {'default': 'red'},
        'cargo_colour': colour_param('Containers') | {'default': 'orange'},
        'cargo_accent': colour_param('Second container colour') | {'default': 'bright green'},
    },
    roles=('hull', 'deck', 'cabin', 'funnel', 'cargo'), build=build_ship,
    coverage=('length', 'container bays', 'colours'), quality='Recognisable cargo ship; the bow is stepped, not sloped')

GENERATORS = [TRAIN, SHIP]
