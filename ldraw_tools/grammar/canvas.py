"""Where parts go: placement list, occupancy (no two parts in the same cell) and an own stud-connectivity check.

Cells are 1 stud x 1 stud x 1 plate. Placements are kept in world cells; `plan()` converts them to the plan schema
(LDU, one step per distinct bottom height so the build manual reads course by course).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import catalog
from .roles import ROLE_NAMES

EPS = 1e-6


class Conflict(ValueError):
    pass


def rotate(x, z, yaw):
    """Same convention as the toolkit's rotation('y', yaw) applied to (x, z)."""
    c, s = round(math.cos(math.radians(yaw)), 9), round(math.sin(math.radians(yaw)), 9)
    return c * x + s * z, -s * x + c * z


def cells_of(box, px, pz, yaw):
    """Integer cells covered by a local box placed with origin (px, pz) and yaw."""
    x0, x1, z0, z1 = box
    pts = [rotate(x, z, yaw) for x in (x0, x1) for z in (z0, z1)]
    xs, zs = [p[0] + px for p in pts], [p[1] + pz for p in pts]
    return [(i, j) for i in range(math.floor(min(xs) + EPS), math.ceil(max(xs) - EPS))
            for j in range(math.floor(min(zs) + EPS), math.ceil(max(zs) - EPS))]


@dataclass
class Item:
    index: int
    code: str
    colour: int
    role: str
    x: float
    z: float
    y0: int
    yaw: int
    h: int
    cells: list
    studs: list
    sockets: bool
    label: str = ''
    host: int = None        # fills a hole of another item (glass, door leaf): no occupancy, joined to it
    top_ldu: float = None   # exact height of the origin when it is not on the plate grid (panes, door leaves)


@dataclass
class Canvas:
    items: list = field(default_factory=list)
    occupied: dict = field(default_factory=dict)
    inventory: object = None
    strict: bool = True

    def put(self, code, colour, role, x, z, y0, yaw=0, *, label='', host=None, occupy=True, top_ldu=None):
        if role not in ROLE_NAMES:
            raise ValueError(f'unknown role {role}')
        spec = catalog.part(code) if code in catalog.PARTS else None
        if spec is None:
            raise KeyError(f'part {code} is not in the grammar catalogue')
        cells = cells_of(spec.box, x, z, yaw)
        if spec.studs is None:
            studs = list(cells)
        else:
            studs = []
            for sx, sz in spec.studs:
                rx, rz = rotate(sx, sz, yaw)
                studs.append((math.floor(rx + x + EPS), math.floor(rz + z + EPS)))
        item = Item(len(self.items), code, colour, role, x, z, y0, yaw, spec.h, cells, studs, spec.sockets, label, host, top_ldu)
        if occupy and host is None:
            for (i, j) in cells:
                for y in range(y0, y0 + spec.h):
                    other = self.occupied.get((i, j, y))
                    if other is not None:
                        if self.strict:
                            o = self.items[other]
                            raise Conflict(f'{code} ({label}) at cell {(i, j, y)} overlaps {o.code} ({o.label})')
                    self.occupied[(i, j, y)] = item.index
        if self.inventory is not None:
            self.inventory.take(code.removesuffix('.dat'), colour)
        self.items.append(item)
        return item

    def free(self, i, j, y):
        return (i, j, y) not in self.occupied

    def all_free(self, cells, y0, h):
        return all((i, j, y) not in self.occupied for (i, j) in cells for y in range(y0, y0 + h))

    def components(self):
        """Own connectivity: studs under sockets at the same height. Glass/leaves join their host."""
        parent = list(range(len(self.items)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            parent[find(a)] = find(b)

        below = {}
        for it in self.items:
            if it.host is not None:
                union(it.index, it.host)
                continue
            for (i, j) in it.cells:
                below.setdefault((i, j, it.y0), []).append(it)
        for it in self.items:
            if it.host is not None:
                continue
            top = it.y0 + it.h
            for (i, j) in it.studs:
                for other in below.get((i, j, top), ()):
                    if other.sockets:
                        union(it.index, other.index)
        groups = {}
        for it in self.items:
            groups.setdefault(find(it.index), []).append(it.index)
        return sorted(groups.values(), key=len, reverse=True)

    def bounds(self):
        xs = [c[0] for it in self.items for c in it.cells]
        zs = [c[1] for it in self.items for c in it.cells]
        return min(xs), max(xs) + 1, min(zs), max(zs) + 1

    def plan(self, name, description, author='ldraw-nova building grammar', centre=None):
        x0, x1, z0, z1 = self.bounds()
        ox, oz = centre if centre else ((x0 + x1) / 2, (z0 + z1) / 2)
        ox, oz = round(ox * 2) / 2, round(oz * 2) / 2
        steps, last = [], None
        for it in sorted(self.items, key=lambda t: (t.y0, t.index)):
            if it.y0 != last:
                steps.append([])
                last = it.y0
            p = dict(id=f'p{it.index + 1:04d}', ref=it.code if it.code[0].isalnum() and '.' in it.code else it.code + '.dat',
                     colour=it.colour, role=it.role, at=[round(20 * (it.x - ox), 3) + 0.0, -(it.top_ldu if it.top_ldu is not None else 8 * (it.y0 + it.h)),
                                                          round(20 * (it.z - oz), 3) + 0.0])
            if it.yaw:
                p['yaw'] = it.yaw % 360 if it.yaw % 360 <= 180 else it.yaw % 360 - 360
            if it.label:
                p['purpose'] = it.label
            steps[-1].append(p)
        return dict(version=1, author=author,
                    sections=[dict(name=name + '.ldr', description=description, steps=steps)])
