"""Footprints as sets of stud cells (i = x right, j = z back; the front is the low-j side) and the wall geometry
derived from them: distance to the outside, wall orientation per cell, facades, convex corners."""
from __future__ import annotations

from collections import deque

FRONT, BACK, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)
NORMALS = {'front': FRONT, 'back': BACK, 'left': LEFT, 'right': RIGHT}
SIDE_OF = {v: k for k, v in NORMALS.items()}
EIGHT = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def rect(i0, j0, w, d):
    return {(i, j) for i in range(i0, i0 + w) for j in range(j0, j0 + d)}


def shape_cells(fp):
    """Cells of a footprint config (see schema). Origin of the bounding box is (0, 0)."""
    w, d = fp['width'], fp['depth']
    cells = rect(0, 0, w, d)
    shape = fp['shape']
    if shape == 'L':
        nw, nd = fp['notch_width'], fp['notch_depth']
        right = 'right' in fp['notch_corner']
        back = 'back' in fp['notch_corner']
        cells -= rect(w - nw if right else 0, d - nd if back else 0, nw, nd)
    elif shape == 'U':
        nw, nd = fp['notch_width'], fp['notch_depth']
        i0 = (w - nw) // 2
        cells -= rect(i0, d - nd if fp['notch_side'] == 'back' else 0, nw, nd)
    elif shape == 'courtyard':
        cw, cd = fp['court_width'], fp['court_depth']
        cells -= rect((w - cw) // 2, (d - cd) // 2, cw, cd)
    return cells


def erode(cells, k):
    for _ in range(k):
        cells = {c for c in cells if all((c[0] + a, c[1] + b) in cells for a, b in EIGHT)}
    return cells


def bbox(cells):
    xs, zs = [c[0] for c in cells], [c[1] for c in cells]
    return min(xs), max(xs) + 1, min(zs), max(zs) + 1


def distances(cells):
    """Chebyshev distance of every cell to the nearest outside cell (1 = touches the outside, diagonals included)."""
    x0, x1, z0, z1 = bbox(cells)
    dist, queue = {}, deque()
    for c in cells:
        if any((c[0] + a, c[1] + b) not in cells for a, b in EIGHT):
            dist[c] = 1
            queue.append(c)
    while queue:
        c = queue.popleft()
        for a, b in EIGHT:
            n = (c[0] + a, c[1] + b)
            if n in cells and n not in dist:
                dist[n] = dist[c] + 1
                queue.append(n)
    return dist


def axial(cells, c, limit):
    """Steps to the outside along each axis: (di, dj), capped at limit + 1."""
    out = []
    for dx, dz in ((1, 0), (0, 1)):
        best = limit + 1
        for sgn in (1, -1):
            k = 1
            while k <= limit and (c[0] + sgn * dx * k, c[1] + sgn * dz * k) in cells:
                k += 1
            best = min(best, k)
        out.append(best)
    return tuple(out)


class Walls:
    """Wall mass of one footprint: which cells are wall, and along which axis each runs.

    'h' cells belong to walls running along x (north/south facades), 'v' to walls along z, 'corner' cells are
    owned alternately by course parity so corners interlock.
    """

    def __init__(self, cells, thickness):
        self.cells, self.t = set(cells), thickness
        self.dist = distances(self.cells)
        self.mass = {c for c, d in self.dist.items() if d <= thickness}
        self.kind = {}
        for c in self.mass:
            di, dj = axial(self.cells, c, thickness)
            near_i, near_j = di <= thickness, dj <= thickness
            if near_j and not near_i:
                self.kind[c] = 'h'
            elif near_i and not near_j:
                self.kind[c] = 'v'
            else:
                self.kind[c] = 'corner'

    def axis(self, c, course):
        k = self.kind[c]
        return k if k != 'corner' else ('h' if course % 2 == 0 else 'v')

    def corner_cells(self):
        """Convex corner blocks only (both axes exposed), not the reflex diagonals."""
        out = set()
        for c in self.mass:
            di, dj = axial(self.cells, c, self.t)
            if di <= self.t and dj <= self.t:
                out.add(c)
        return out


def facades(cells, t, margin):
    """Straight runs of wall facing out: [dict(side, normal, line, a, b, usable=(a2, b2))] along the facade axis.

    line is the row (front/back) or column (left/right) of the outermost wall cells.
    """
    out = []
    for side, (nx, nz) in NORMALS.items():
        outer = {c for c in cells if (c[0] + nx, c[1] + nz) not in cells}
        groups = {}
        for (i, j) in outer:
            key, pos = (j, i) if nz else (i, j)
            groups.setdefault(key, []).append(pos)
        for key, poss in sorted(groups.items()):
            poss.sort()
            start = prev = poss[0]
            for p in poss[1:] + [None]:
                if p is None or p != prev + 1:
                    a, b = start, prev + 1
                    out.append(dict(side=side, normal=(nx, nz), line=key, a=a, b=b, usable=(a + t + margin, b - t - margin)))
                    start = p
                prev = p if p is not None else prev
    return out


def convex_corners(cells):
    """[(cell, (sx, sz))]: convex corner cells with the outward diagonal direction of each."""
    out = []
    for c in sorted(cells):
        for sx in (-1, 1):
            for sz in (-1, 1):
                if ((c[0] + sx, c[1]) not in cells and (c[0], c[1] + sz) not in cells and (c[0] + sx, c[1] + sz) not in cells):
                    out.append((c, (sx, sz)))
    return out
