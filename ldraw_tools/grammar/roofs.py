"""Roofs: gable, hip (also tower pyramids) and flat decks with parapets or battlements.

Pitched roofs are hollow shells of 45 degree slopes: each tier sits on the studs of the one below, gable ends are
closed by stepped wall bricks, and a ridge of plates and tiles caps the top. All placed with real slope parts.
"""
from __future__ import annotations

from . import catalog, tiling


class Frame:
    """Local roof coordinates: u along the ridge (0..W), v across it (0..D); maps to world cells."""

    def __init__(self, i0, j0, w, d, along_x):
        self.i0, self.j0, self.along_x = i0, j0, along_x
        self.W, self.D = (w, d) if along_x else (d, w)

    def point(self, u, v):
        """World (x, z) of local point (u, v) in studs."""
        return (self.i0 + u, self.j0 + v) if self.along_x else (self.i0 + v, self.j0 + self.W - u)

    def cell(self, u, v):
        x, z = self.point(u + .5, v + .5)
        return int(x // 1), int(z // 1)

    def yaw(self, local):
        return local if self.along_x else local + 90

    def place(self, ctx, code, colour, role, u, v, y0, local_yaw, label):
        x, z = self.point(u, v)
        ctx.canvas.put(code, colour, role, x, z, y0, self.yaw(local_yaw), label=label)

    def rect_cells(self, u0, v0, w, d):
        return [self.cell(u, v) for u in range(u0, u0 + w) for v in range(v0, v0 + d)]


def slope_run(ctx, frame, colour, role, v, u0, length, y0, yaw_local, label, prev_joints):
    """A row of slopes along u from u0, 2 deep starting at v (front: low side at v; back: low side at v+2)."""
    lengths = tiling.tile_run(length, u0, prev_joints, tiling.SLOPE_LENGTHS, cost={4: 1.0, 3: 1.35, 2: 1.0, 1: 1.8})
    pos, joints = u0, set()
    for n in lengths:
        centre = pos + n / 2
        if yaw_local == 0:           # front: low side toward -v, high stud row at v+1 (origin on the stud cell)
            frame.place(ctx, catalog.SLOPE45[n], colour, role, centre, v + 1.5, y0, 0, label)
        else:                        # back: rotated half a turn, the stud row at v+0.5
            frame.place(ctx, catalog.SLOPE45[n], colour, role, centre, v + .5, y0, 180, label)
        joints.update({pos, pos + n})
        pos += n
    return joints


def cap(ctx, frame, colour, role, u0, v0, w, d, y_plate, with_tiles=True, keep_free=()):
    """Plates over the ridge region and tiles on top, skipping cells in keep_free for the tiles."""
    cells = frame.rect_cells(u0, v0, w, d)
    for (code, i, j, pw, pd) in tiling.rect_cover(cells, 'plate'):
        _put_cover(ctx, code, colour, role, i, j, pw, pd, y_plate, 'ridge plate')
    if with_tiles:
        tcells = [c for c in cells if c not in keep_free]
        for (code, i, j, pw, pd) in tiling.rect_cover(tcells, 'tile'):
            _put_cover(ctx, code, colour, role, i, j, pw, pd, y_plate + 1, 'ridge tile')


def _put_cover(ctx, code, colour, role, i, j, pw, pd, y0, label):
    if pd > pw:
        ctx.canvas.put(code, colour, role, i + pw / 2, j + pd / 2, y0, 90, label=label)
    else:
        ctx.canvas.put(code, colour, role, i + pw / 2, j + pd / 2, y0, 0, label=label)


def tier_count(D, tiers):
    full = max(1, (D - 2) // 2)
    return min(full, tiers) if tiers else full


def gable(ctx, rect, y_top, st):
    """rect = (i0, j0, w, d) of the top storey's walls; y_top = plate height of the band top."""
    cfg = ctx.cfg['roof']
    i0, j0, w, d = rect
    along_x = (w >= d) if cfg['ridge'] == 'auto' else cfg['ridge'] == 'x'
    o, g = cfg['overhang'], cfg['verge']
    if along_x:
        frame = Frame(i0 - g, j0 - o, w + 2 * g, d + 2 * o, True)
    else:
        frame = Frame(i0 - o, j0 - g, w + 2 * o, d + 2 * g, False)
    W, D = frame.W, frame.D
    colour = ctx.code(cfg['colour'])
    wall = ctx.code(ctx.cfg['walls']['colour'])
    T = tier_count(D, cfg['tiers'])
    if D < 4:
        raise ValueError('roof too narrow for a gable')
    prev_front = prev_back = set()
    for t in range(T):
        y0 = y_top + 3 * t
        prev_front = slope_run(ctx, frame, colour, 'roof', t, 0, W, y0, 0, f'roof tier {t} front', prev_front)
        prev_back = slope_run(ctx, frame, colour, 'roof', D - t - 2, 0, W, y0, 180, f'roof tier {t} back', prev_back)
        # gable end walls fill the triangle between the slopes at both ends (they sit on the wall cells)
        mid0, mid1 = t + 2, D - t - 2
        if mid1 > mid0:
            for u in (g, W - g - 1):
                _gable_wall(ctx, frame, u, mid0, mid1, y0, wall, t)
    ridge_v0 = T
    ridge_d = D - 2 * T
    keep = set()
    chim = ctx.cfg['details']['chimney']
    frame.rect_cells(0, ridge_v0, W, ridge_d)
    ctx.ridge = dict(frame=frame, y=y_top + 3 * T, v0=ridge_v0, d=ridge_d, W=W)
    if chim != 'none':
        cu = {'left': max(g + 1, W // 4 - 1), 'centre': (W - 2) // 2, 'right': W - max(g + 1, W // 4 - 1) - 2}[chim]
        keep = set(frame.rect_cells(cu, ridge_v0 + (ridge_d - 2) // 2, 2, 2))
        ctx.ridge['chimney'] = (cu, ridge_v0 + (ridge_d - 2) // 2)
    cap(ctx, frame, colour, 'roof', 0, ridge_v0, W, ridge_d, y_top + 3 * T, True, keep)
    return frame


def _gable_wall(ctx, frame, u, v0, v1, y0, colour, tier):
    n = v1 - v0
    start = v0
    for length in tiling.tile_run(n, start, set()):
        x, z = frame.point(u + .5, start + length / 2)
        ctx.canvas.put(catalog.BRICK[length], colour, 'wall', x, z, y0, 90 if frame.along_x else 0, label=f'gable end tier {tier}')
        start += length


def hip(ctx, rect, y_top, colour, role, tiers=0, label='hip roof', ring_only=False, finial=False):
    """Hip roof / pyramid on rect = (i0, j0, w, d): rings of slopes with double-convex corners."""
    i0, j0, w, d = rect
    T = max(1, (min(w, d) - 2) // 2)
    if tiers:
        T = min(T, tiers)
    if ring_only:
        T = 1              # a skirt: one ring of slopes round the edge, nothing over the middle
    for t in range(T):
        y0 = y_top + 3 * t
        a0, a1 = t, w - t                    # ring extents along x
        b0, b1 = t, d - t
        # corners are 2x2 double-convex slopes whose low corner points outward
        _hip_corners(ctx, i0, j0, a0, a1, b0, b1, y0, colour, role, label)
        prev = set()
        # front and back rows between the corners
        for (row, yaw_local) in ((b0, 0), (b1 - 2, 180)):
            n = (a1 - 2) - (a0 + 2)
            if n > 0:
                lengths = tiling.tile_run(n, a0 + 2, prev, tiling.SLOPE_LENGTHS, cost={4: 1.0, 3: 1.35, 2: 1.0, 1: 1.8})
                pos = a0 + 2
                for length in lengths:
                    zc = row + (1.5 if yaw_local == 0 else .5)
                    ctx.canvas.put(catalog.SLOPE45[length], colour, role, i0 + pos + length / 2, j0 + zc, y0, yaw_local, label=label)
                    pos += length
        for (col, yaw) in ((a0, 90), (a1 - 2, -90)):
            n = (b1 - 2) - (b0 + 2)
            if n > 0:
                lengths = tiling.tile_run(n, b0 + 2, set(), tiling.SLOPE_LENGTHS, cost={4: 1.0, 3: 1.35, 2: 1.0, 1: 1.8})
                pos = b0 + 2
                for length in lengths:
                    xc = col + (1.5 if yaw == 90 else .5)
                    ctx.canvas.put(catalog.SLOPE45[length], colour, role, i0 + xc, j0 + pos + length / 2, y0, yaw, label=label)
                    pos += length
    if ring_only:
        return dict(top=y_top + 3, inner=None)
    inner = (T, T, w - 2 * T, d - 2 * T)
    cells = [(i0 + i, j0 + j) for i in range(inner[0], inner[0] + inner[2]) for j in range(inner[1], inner[1] + inner[3])]
    y = y_top + 3 * T
    for (code, i, j, pw, pd) in tiling.rect_cover(cells, 'plate'):
        _put_cover(ctx, code, colour, role, i, j, pw, pd, y, 'ridge plate')
    spire = (i0 + inner[0] + inner[2] // 2 - (1 if inner[2] % 2 == 0 else 0), j0 + inner[1] + inner[3] // 2 - (1 if inner[3] % 2 == 0 else 0)) if finial else None
    for (code, i, j, pw, pd) in tiling.rect_cover([c for c in cells if c != spire], 'tile'):
        _put_cover(ctx, code, colour, role, i, j, pw, pd, y + 1, 'ridge tile')
    if spire:
        ctx.canvas.put(catalog.ROUND_BRICK_1, colour, role, spire[0] + .5, spire[1] + .5, y + 1, 0, label='finial')
    return dict(top=y + 2, inner=inner)


def _hip_corners(ctx, i0, j0, a0, a1, b0, b1, y0, colour, role, label):
    """3045 has its stud at the origin and its body toward +x,-z; each yaw turns the low corner to a ring corner."""
    spots = [(a1 - 2, b0 + 1, 0), (a0 + 1, b0 + 1, 90), (a0 + 1, b1 - 2, 180), (a1 - 2, b1 - 2, 270)]
    for (sx, sz, yaw) in spots:                       # (sx, sz) is the stud cell
        ctx.canvas.put(catalog.CORNER45, colour, role, i0 + sx + .5, j0 + sz + .5, y0, yaw, label=label + ' corner')
