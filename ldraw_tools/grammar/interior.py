"""Rooms: a partition wall with a doorway on every storey, bonded like the outer walls."""
from __future__ import annotations

from . import catalog, footprint, parts


def partition(ctx, st):
    cfg = ctx.cfg
    if cfg['storeys']['interior'] != 'partition' or st.kind != 'main':
        return
    x0, x1, z0, z1 = footprint.bbox(st.cells - st.blocked)
    t = ctx.t
    j = z0 + (z1 - z0) // 2
    lo, hi = x0 + t, x1 - t                   # interior cells of the row
    if hi - lo < 6 or (z1 - z0) < 2 * t + 4:
        return
    mid = (lo + hi) // 2
    door = (mid - 2, mid + 2)
    colour = ctx.code(cfg['storeys']['band_colour'])
    height = min(6, st.courses - 1)
    prev = set()
    for course in range(st.courses):
        y0 = st.y_floor + 3 * course
        joints = set()
        spans = [(lo, door[0]), (door[1], hi)] if course < height else [(lo, hi)]
        for (a, b) in spans:
            pos = a
            for length in parts.fit_run(ctx, b - a, a, prev, catalog.BRICK, colour):
                if all(ctx.canvas.free(pos + k, j, y0 + h) for k in range(length) for h in range(3)):
                    ctx.canvas.put(catalog.BRICK[length], colour, 'structure', pos + length / 2, j + .5, y0, 0, label=f'partition s{st.index}')
                joints |= {pos, pos + length}
                pos += length
        prev = joints
