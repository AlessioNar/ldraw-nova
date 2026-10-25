"""Real parts the grammar uses, with the dimensions it needs (studs and plates) and an owned-parts inventory.

Sizes come from the library's own bounds (checked in tests). Coordinates: x right, z back (front is -z), height in
plates (1 plate = 8 LDU, 1 brick = 3 plates). A part's local box is given relative to its LDraw origin.
"""
from __future__ import annotations

from dataclasses import dataclass, field

BRICK = {1: '3005', 2: '3004', 3: '3622', 4: '3010', 6: '3009', 8: '3008'}
PLATE = {1: '3024', 2: '3023b', 3: '3623', 4: '3710', 6: '3666', 8: '3460'}
PLATE2 = {2: '3022', 3: '3021', 4: '3020', 6: '3795', 8: '3034'}
TILE = {1: '3070b', 2: '3069b', 3: '63864', 4: '2431', 6: '6636'}
TILE2 = {2: '3068b', 4: '87079'}
SLOPE45 = {1: '3040b', 2: '3039', 3: '3038', 4: '3037'}
CORNER45 = '3045'
ROUND_BRICK_1 = '3062b'
ROUND_PLATE_1 = '85861'   # open-stud round plate: 6141 (and its old number 4073) does not mate with plates in the LDCad shadow data
ROUND_BRICK_4 = '87081'
ROUND_PLATE_4 = '60474'
CONE_4 = '3943b'
CONE_1 = '4589'
WINDOW_4X3, PANE_4X3 = '60594', '60608'
WINDOW_2X2, WINDOW_2X3 = '60592c01', '60593c01'
WINDOW_ARCH, PANE_ARCH = '30044', '30046'
DOOR_FRAME, DOOR_GLASS_FRAME = '60596', '60596'
DOOR_LEAF = {'plain': '60623', 'smooth': '60616a', 'glazed': '60797c04', 'barred': '60621'}
SHOP_PANE = '57895'


@dataclass(frozen=True)
class Part:
    code: str
    box: tuple          # local (x0, x1, z0, z1) in studs, relative to the origin
    h: int              # height in plates
    studs: tuple = None  # local stud centres (x, z); None = every cell of the box, () = none
    sockets: bool = True


def _bar(n, depth=1, h=3, studs=True):
    return Part('', (-n / 2, n / 2, -depth / 2, depth / 2), h, None if studs else ())


def _slope(width):
    cells = tuple((x - (width - 1) / 2, 0.0) for x in range(width))
    return Part('', (-width / 2, width / 2, -1.5, 0.5), 3, cells)


PARTS = {}


def _add(code, part):
    PARTS[code] = Part(code, part.box, part.h, part.studs, part.sockets)


for n, c in BRICK.items():
    _add(c, _bar(n))
for n, c in PLATE.items():
    _add(c, _bar(n, h=1))
for n, c in PLATE2.items():
    _add(c, _bar(n, 2, 1))
for n, c in TILE.items():
    _add(c, _bar(n, h=1, studs=False))
for n, c in TILE2.items():
    _add(c, _bar(n, 2, 1, studs=False))
for n, c in SLOPE45.items():
    _add(c, _slope(n))
_add('3003', _bar(2, 2))
TECHNIC_BRICK = '3700'
_add(TECHNIC_BRICK, _bar(2))
_add(CORNER45, Part('', (-0.5, 1.5, -1.5, 0.5), 3, ((0.0, 0.0),)))
_add(ROUND_BRICK_1, _bar(1))
_add(ROUND_PLATE_1, _bar(1, h=1))
_add(ROUND_BRICK_4, _bar(4, 4))
_add(ROUND_PLATE_4, _bar(4, 4, 1))
_add(CONE_4, Part('', (-2, 2, -2, 2), 6, ()))
_add('3941', _bar(2, 2))
_add(WINDOW_4X3, _bar(4, 1, 9))
_add(WINDOW_2X2, _bar(2, 1, 6))
_add(WINDOW_2X3, _bar(2, 1, 9))
_add(WINDOW_ARCH, _bar(2, 1, 8))
_add(DOOR_FRAME, _bar(4, 1, 18))
_add('6141', _bar(1, 1, 1))
_add('4589', _bar(1, 1, 3, studs=False))


def part(code):
    return PARTS[code]


# Windows, from widest to narrowest, with their size in studs wide x courses tall (30044 also needs a 1-plate sill).
WINDOWS = {
    'rect': dict(code=WINDOW_4X3, width=4, courses=3, label='window 1x4x3'),
    'small': dict(code=WINDOW_2X2, width=2, courses=2, label='window 1x2x2'),
    'tall': dict(code=WINDOW_2X3, width=2, courses=3, label='window 1x2x3'),
    'arch': dict(code=WINDOW_ARCH, width=2, courses=3, label='window 1x2x2 2/3 with rounded top'),
    'slit': dict(code=None, width=1, courses=2, label='arrow slit'),
    'wide': dict(code=WINDOW_4X3, width=8, courses=3, label='two 1x4x3 windows side by side'),
}
FALLBACK = {'wide': 'rect', 'rect': 'tall', 'tall': 'small', 'arch': 'small'}

# Hex colours for the substitution search when only owned parts may be used.
RGB = {0: 0x1B2A34, 1: 0x1E5AA8, 2: 0x00852B, 4: 0xC91A09, 5: 0xC870A0, 9: 0x7DC1FF, 10: 0x4B9F4A, 14: 0xF2CD37,
       15: 0xFFFFFF, 19: 0xE4CD9E, 25: 0xFE8A18, 26: 0x923978, 27: 0xBBE90B, 28: 0x958A73, 70: 0x582A12,
       71: 0xA0A5A9, 72: 0x6C6E68, 73: 0x5C9DD1, 85: 0x3F3691, 86: 0x7C503A, 92: 0xCC8E69, 288: 0x184632,
       272: 0x0D325B, 308: 0x352100, 320: 0x720E0F, 322: 0x36AEBF, 321: 0x1498D7, 334: 0xBB9500, 378: 0x708E7C,
       484: 0x91501C, 47: 0xFCFCFC, 10375: 0x635F52, 36: 0xC91A09, 34: 0x237841, 33: 0x0020A0, 46: 0xF5CD2F,
       57: 0xF08F1C, 43: 0x68BCC5, 212: 0x9DC3F7, 226: 0xFFF07A, 191: 0xFCAC00, 330: 0x77774E, 17: 0xC2DAB8,
       18: 0xFDF080, 12: 0xF09090, 13: 0xFF87A2}


def colour_distance(a, b):
    ra, rb = RGB.get(a, 0x808080), RGB.get(b, 0x808080)
    return sum(((ra >> s & 255) - (rb >> s & 255)) ** 2 for s in (16, 8, 0)) ** .5


@dataclass
class Inventory:
    """Owned parts: rows of (part code, colour code, quantity). mode 'prefer' orders choices by ownership and
    reports shortages; 'only' also restricts tilings to owned lengths and substitutes colours that are not owned.
    """
    rows: dict
    mode: str = 'prefer'
    used: dict = field(default_factory=dict)
    shortages: dict = field(default_factory=dict)
    substitutions: list = field(default_factory=list)

    @classmethod
    def from_rows(cls, rows, mode='prefer'):
        from .colours import colour_code
        table = {}
        for row in rows:
            code = str(row['part']).removesuffix('.dat')
            colour = colour_code(row['colour'])
            table[(code, colour)] = table.get((code, colour), 0) + int(row.get('qty', 1))
        return cls(table, mode)

    def colours(self):
        return {c for (_, c) in self.rows}

    def left(self, code, colour):
        return self.rows.get((code, colour), 0) - self.used.get((code, colour), 0)

    def has_colour(self, colour, codes=None):
        return any(c == colour and (codes is None or p in codes) and self.left(p, c) > 0 for (p, c) in self.rows)

    def nearest_colour(self, colour, codes=None):
        pool = sorted({c for (p, c) in self.rows if (codes is None or p in codes) and self.left(p, c) > 0})
        return min(pool, key=lambda c: (colour_distance(colour, c), c)) if pool else colour

    def substitute(self, kind, wanted, used, reason):
        """Record a swap in 'only' mode (once per wanted/used pair): kind is 'colour' or 'part'."""
        entry = dict(kind=kind, wanted=wanted, used=used, reason=reason)
        if entry not in self.substitutions:
            self.substitutions.append(entry)

    def take(self, code, colour):
        key = (code, colour)
        self.used[key] = self.used.get(key, 0) + 1
        if self.used[key] > self.rows.get(key, 0):
            self.shortages[key] = self.used[key] - self.rows.get(key, 0)

    def report(self):
        return dict(mode=self.mode,
                    used=[dict(part=p, colour=c, qty=q) for (p, c), q in sorted(self.used.items())],
                    shortages=[dict(part=p, colour=c, missing=q) for (p, c), q in sorted(self.shortages.items())],
                    substitutions=list(self.substitutions),
                    complete=not self.shortages)

# Infill parts (panes, door leaves) are hosted by a frame and take no space of their own.
for _code in (PANE_4X3, PANE_ARCH, SHOP_PANE, *DOOR_LEAF.values()):
    PARTS[_code] = Part(_code, (-0.5, 0.5, -0.5, 0.5), 3, (), False)
