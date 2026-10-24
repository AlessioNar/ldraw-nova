"""Cutting runs and areas into real part lengths: running bond for walls, plates for floors."""
from __future__ import annotations

from . import catalog

BRICK_LENGTHS = (8, 6, 4, 3, 2, 1)
COST = {8: 1.15, 6: 1.0, 4: 1.0, 3: 1.45, 2: 1.0, 1: 2.2}
JOINT = 5.0      # penalty for a vertical joint that lines up with a joint in the course below (or the row beside)
SLOPE_LENGTHS = (4, 3, 2, 1)


def tile_run(n, start, avoid, lengths=BRICK_LENGTHS, cost=None, owned=None):
    """Best split of a run of n cells beginning at absolute position `start` into part lengths.

    avoid: absolute positions of joints in the course below / the neighbouring row; a joint there is punished so
    consecutive courses overlap (running bond). Returns the list of lengths. Deterministic: ties prefer longer
    parts first. `owned(length)` may return a penalty for lengths the builder lacks.
    """
    cost = cost or COST
    best = [(0.0, ())] + [(float('inf'), ())] * n
    for i in range(1, n + 1):
        for length in lengths:
            if length > i or best[i - length][0] == float('inf'):
                continue
            c = best[i - length][0] + cost[length]
            if owned:
                c += owned(length)
            if i < n and (start + i) in avoid:
                c += JOINT
            if c < best[i][0] - 1e-9:
                best[i] = (c, best[i - length][1] + (length,))
    if best[n][0] == float('inf'):
        raise ValueError(f'cannot tile {n} with {lengths}')
    return list(best[n][1])


def rect_cover(cells, kind='plate', longest=8):
    """Cover a set of (i, j) cells with plates (or tiles): [(code, i0, j0, w, d)] with d in {1, 2} along z.

    Wide plates first (2 x L), then 1 x L; deterministic scan order. Long side is always along x.
    """
    cells = set(cells)
    done = set()
    one = catalog.PLATE if kind == 'plate' else catalog.TILE
    two = catalog.PLATE2 if kind == 'plate' else catalog.TILE2
    two_lengths = sorted((n for n in two if n <= longest), reverse=True)
    one_lengths = sorted((n for n in one if n <= longest), reverse=True)
    out = []
    for (i, j) in sorted(cells, key=lambda c: (c[1], c[0])):
        if (i, j) in done:
            continue
        run2 = 0
        while (i + run2, j) in cells and (i + run2, j) not in done and (i + run2, j + 1) in cells \
                and (i + run2, j + 1) not in done and run2 < max(two_lengths):
            run2 += 1
        length2 = next((n for n in two_lengths if n <= run2), 0)
        if length2:
            out.append((two[length2], i, j, length2, 2))
            for a in range(length2):
                done.update({(i + a, j), (i + a, j + 1)})
            continue
        run1 = 0
        while (i + run1, j) in cells and (i + run1, j) not in done and run1 < max(one_lengths):
            run1 += 1
        length1 = next(n for n in one_lengths if n <= run1)
        rise = 0
        while (i, j + rise) in cells and (i, j + rise) not in done and rise < max(one_lengths):
            rise += 1
        lengthv = next(n for n in one_lengths if n <= rise)
        if lengthv > length1:
            out.append((one[lengthv], i, j, 1, lengthv))       # lying along z
            for a in range(lengthv):
                done.add((i, j + a))
            continue
        out.append((one[length1], i, j, length1, 1))
        for a in range(length1):
            done.add((i + a, j))
    return out


def _footprint(entry):
    _, i, j, w, d = entry
    return {(i + a, j + b) for a in range(w) for b in range(d)}


def cover_variants(cells):
    """Second-layer covers to try, from the preferred one outward: crossing orientation, plate length, and where
    the seams fall (1-3 lines peeled off one edge first)."""
    cells = set(cells)
    if not cells:
        return
    for offset in (1, 2, 3):
        for axis in (0, 1):
            for transpose in (True, False):
                for longest in (6, 4, 8, 3):
                    for lead in ('first', 'last'):
                        src = {(j, i) for (i, j) in cells} if transpose else cells
                        vals = sorted({c[axis] for c in src})
                        lines = set(vals[:offset] if lead == 'first' else vals[-offset:])
                        out = []
                        for part in ({c for c in src if c[axis] not in lines}, {c for c in src if c[axis] in lines}):
                            out += rect_cover(part, 'plate', longest=longest)
                        if transpose:
                            out = [(code, b, a, d, w) for (code, a, b, w, d) in out]
                        yield out


def two_layers(cells, support, upper_cells=None):
    """(lower, upper) plate covers of `cells` so that together they form one piece that touches `support` cells.

    The lower layer is the plain cover; the upper layer is the first variant whose plates, with the lower ones
    that they overlap, form a single connected group containing a plate on a support cell.
    """
    lower = rect_cover(cells, 'plate')
    for upper in cover_variants(cells if upper_cells is None else upper_cells):
        pieces = [(_footprint(e), 0) for e in lower] + [(_footprint(e), 1) for e in upper]
        parent = list(range(len(pieces)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        owner = {}
        for k, (fp, layer) in enumerate(pieces):
            for c in fp:
                owner.setdefault((c, 1 - layer), []).append(k)
        for k, (fp, layer) in enumerate(pieces):
            for c in fp:
                for other in owner.get((c, layer), ()):
                    parent[find(k)] = find(other)
        roots = {find(k) for k in range(len(pieces))}
        if len(roots) == 1 and any(fp & support for fp, layer in pieces if layer == 0):
            return lower, upper
    return lower, rect_cover_t(cells if upper_cells is None else upper_cells)


def rect_cover_t(cells, kind='plate'):
    """Like rect_cover but plates lie along z, are at most 6 long and start one stud off."""
    cells = set(cells)
    if not cells:
        return []
    first = min(c[0] for c in cells)
    column = {c for c in cells if c[0] == first}
    out = []
    for part in (cells - column, column):
        out += [(code, b, a, d, w) for (code, a, b, w, d) in rect_cover({(j, i) for (i, j) in part}, kind, longest=6)]
    return out
