"""Rooms: a partition wall with a doorway on every storey, bonded like the outer walls, and optional furniture."""
from __future__ import annotations

from . import catalog, footprint, parts
from .openings import cell_of


def partition(ctx, st):
    cfg = ctx.cfg
    if cfg['storeys']['interior'] == 'none' or st.kind != 'main':
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
    if cfg['storeys']['interior'] == 'furnished':
        furnish(ctx, st, lo, hi, z0 + t, j, j + 1, z1 - t, (mid - 3, mid + 3))


# ---- furniture: each piece is (width along x, depth along z, height in plates, builder); builders stand on the floor studs

def _table_set(ctx, i, j, y):
    """A 3x2 table with a chair at each end (seat and a two-brick back): 7 studs wide."""
    wood, seat = ctx.code('reddish brown'), ctx.code('dark tan')
    for dx, dz in ((2, 0), (4, 0), (2, 1), (4, 1)):
        ctx.canvas.put(catalog.ROUND_BRICK_1, wood, 'detail', i + dx + .5, j + dz + .5, y, 0, label='table leg')
    ctx.canvas.put(catalog.PLATE2[3], wood, 'detail', i + 3.5, j + 1, y + 3, 0, label='table top')
    for seat_x, back_x in ((1, 0), (5, 6)):
        ctx.canvas.put(catalog.BRICK[1], seat, 'detail', i + seat_x + .5, j + .5, y, 0, label='chair seat')
        for k in range(2):
            ctx.canvas.put(catalog.BRICK[1], seat, 'detail', i + back_x + .5, j + .5, y + 3 * k, 0, label='chair back')


def _bed(ctx, i, j, y):
    """A 2x4 bed: frame brick, blanket plate over three studs and a pillow plate at the head."""
    ctx.canvas.put(catalog.BRICK_2X4, ctx.code('reddish brown'), 'detail', i + 2, j + 1, y, 0, label='bed')
    ctx.canvas.put(catalog.PLATE2[3], ctx.code('dark blue'), 'detail', i + 2.5, j + 1, y + 3, 0, label='blanket')
    ctx.canvas.put(catalog.PLATE[2], ctx.code('white'), 'detail', i + .5, j + 1, y + 3, 90, label='pillow')


def _cupboard(ctx, i, j, y):
    """A 1x4 cupboard standing along a side wall: two bricks and a top plate."""
    for k in range(2):
        ctx.canvas.put(catalog.BRICK[4], ctx.code('dark tan'), 'detail', i + .5, j + 2, y + 3 * k, 90, label='cupboard')
    ctx.canvas.put(catalog.PLATE[4], ctx.code('reddish brown'), 'detail', i + .5, j + 2, y + 6, 90, label='cupboard top')


def _counter(ctx, i, j, y):
    """A shop counter: two 1x4 bricks with a tile top."""
    for k in range(2):
        ctx.canvas.put(catalog.BRICK[4], ctx.code('white'), 'detail', i + 2 + 4 * k, j + .5, y, 0, label='counter')
        ctx.canvas.put(catalog.TILE[4], ctx.code('reddish brown'), 'detail', i + 2 + 4 * k, j + .5, y + 3, 0, label='counter top')


PIECES = {'table': (7, 2, 6, _table_set), 'bed': (4, 2, 4, _bed), 'cupboard': (1, 4, 7, _cupboard), 'counter': (8, 1, 4, _counter)}


def _place(ctx, kind, i, j, y, keep_clear):
    w, d, h, build = PIECES[kind]
    cells = footprint.rect(i, j, w, d)
    if cells & keep_clear or not ctx.canvas.all_free(cells, y, h) or any(ctx.canvas.free(c[0], c[1], y - 1) for c in cells):
        return False
    build(ctx, i, j, y)
    return True


def furnish(ctx, st, lo, hi, fz0, fz1, bz0, bz1, doorway):
    """Furniture for the two rooms either side of the partition. Every piece is tried at a few spots and skipped when it
    does not fit, so a small building simply gets fewer pieces; the doorways and the space behind the front door stay clear."""
    y = st.y_floor
    keep_clear = {(x, z) for x in range(*doorway) for z in range(fz1 - 2, bz0 + 2)}
    door = ctx.door if st.index == 0 else None
    if door is not None:
        keep_clear |= {cell_of(door, u, k) for u in range(door['u0'] - 1, door['u0'] + 5) for k in range(ctx.t, ctx.t + 3)}
    centre = (lo + hi) // 2
    if st.index == 0:
        back = (('table', centre - 3, bz0 + 3), ('cupboard', lo, bz0))
        front = (('counter', hi - 9, fz0 + 2), ('counter', lo + 1, fz0 + 2))
    else:
        back = (('bed', lo, bz1 - 2), ('cupboard', hi - 1, bz0))
        front = (('table', centre - 3, fz0 + max(0, (fz1 - fz0 - 2) // 2)), ('cupboard', lo, fz0))
    placed_counter = False
    for kind, i, j in (*back, *front):
        if kind == 'counter':
            if placed_counter:
                continue
            placed_counter = _place(ctx, kind, i, j, y, keep_clear)
        else:
            _place(ctx, kind, i, j, y, keep_clear)
