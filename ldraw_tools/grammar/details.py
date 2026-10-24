"""Add-ons: base plate and path, porch, balcony, chimney."""
from __future__ import annotations

from . import catalog, footprint, tiling
from .roofs import _put_cover


def base_plate(ctx):
    cfg = ctx.cfg['base']
    if cfg['kind'] == 'none':
        ctx.y_ground = 0
        ctx.base_rect = None
        return
    ctx.y_ground = 2
    cells = set()
    for st in ctx.storeys_preview:
        cells |= st
    x0, x1, z0, z1 = footprint.bbox(cells)
    m = cfg['margin']
    ctx.base_rect = (x0 - m, z0 - m, x1 - x0 + 2 * m, z1 - z0 + 2 * m)
    colour = ctx.code(cfg['colour'])
    i0, j0, w, d = ctx.base_rect
    # Two layers of plate strips laid across each other, each strip's joints staggered against the last.
    for layer, (length, breadth, yaw) in enumerate(((w, d, 0), (d, w, 90))):
        prev, pos = set(), 0
        while pos < breadth:
            depth = 2 if breadth - pos >= 2 else 1
            lengths = tiling.tile_run(length, 0, prev, (8, 6, 4, 3, 2) if depth == 2 else (8, 6, 4, 3, 2, 1))
            joints, at = set(), 0
            for n in lengths:
                code = (catalog.PLATE2 if depth == 2 else catalog.PLATE)[n]
                if yaw == 0:
                    ctx.canvas.put(code, colour, 'base', i0 + at + n / 2, j0 + pos + depth / 2, layer, 0, label='base')
                else:
                    ctx.canvas.put(code, colour, 'base', i0 + pos + depth / 2, j0 + at + n / 2, layer, 90, label='base')
                joints |= {at, at + n}
                at += n
            prev, pos = joints, pos + depth


def path(ctx):
    cfg = ctx.cfg['base']
    door = ctx.door
    if cfg['kind'] == 'none' or not cfg['path'] or door is None:
        return
    nx, nz = door['normal']
    centre = door['u0'] + 2
    pw = cfg['path_width']
    lateral = range(centre - pw // 2, centre - pw // 2 + pw)
    bx0, bz0, bw, bd = ctx.base_rect
    cells = set()
    line = door['line']
    for u in lateral:
        k = 1
        while True:
            c = {(0, -1): (u, line - k), (0, 1): (u, line + k), (-1, 0): (line - k, u), (1, 0): (line + k, u)}[(nx, nz)]
            if not (bx0 <= c[0] < bx0 + bw and bz0 <= c[1] < bz0 + bd):
                break
            cells.add(c)
            k += 1
    cells = {c for c in cells if ctx.canvas.free(c[0], c[1], ctx.y_ground)}
    colour = ctx.code(cfg['path_colour'])
    for (code, i, j, w, d) in tiling.rect_cover(cells, 'tile'):
        _put_cover(ctx, code, colour, 'base', i, j, w, d, ctx.y_ground, 'path')


def porch(ctx):
    cfg = ctx.cfg['details']
    door = ctx.door
    if not cfg['porch'] or door is None or door['side'] != 'front':
        return
    u0, line = door['u0'], door['line']
    colour = ctx.code(cfg['porch_colour'])
    roof = ctx.code(ctx.cfg['roof']['colour'])
    y = ctx.y_ground
    cells = footprint.rect(u0 - 1, line - 2, 6, 2)
    cells = {c for c in cells if ctx.canvas.all_free([c], y, 20)}
    if len(cells) < 12:
        ctx.note('porch skipped: no room in front of the door')
        return
    for (code, i, j, w, d) in tiling.rect_cover(cells, 'plate'):
        _put_cover(ctx, code, colour, 'detail', i, j, w, d, y, 'porch floor')
    for (pi, pj) in ((u0 - 1, line - 2), (u0 + 4, line - 2)):
        for k in range(5):
            ctx.canvas.put(catalog.BRICK[1], colour, 'detail', pi + .5, pj + .5, y + 1 + 3 * k, 0, label='porch pillar')
        ctx.canvas.put(catalog.PLATE[1], colour, 'detail', pi + .5, pj + .5, y + 16, 0, label='porch pillar')
        ctx.canvas.put(catalog.PLATE[1], colour, 'detail', pi + .5, pj + .5, y + 17, 0, label='porch pillar')
    top = footprint.rect(u0 - 1, line - 2, 6, 2)
    for (code, i, j, w, d) in tiling.rect_cover(top, 'plate'):
        _put_cover(ctx, code, roof, 'detail', i, j, w, d, y + 18, 'porch canopy')
    for (code, i, j, w, d) in tiling.rect_cover(top, 'tile'):
        _put_cover(ctx, code, roof, 'detail', i, j, w, d, y + 19, 'porch canopy tile')


def balcony_cells(ctx):
    """Cells of the balcony floor for the band under the first upper storey (cut with the band plates)."""
    cfg = ctx.cfg['details']
    door = ctx.door
    if not cfg['balcony'] or door is None or door['side'] != 'front' or len(ctx.storeys) < 2:
        return set()
    u0, line = door['u0'], door['line']
    return footprint.rect(u0 - 1, line - 1, 6, 1)


def balcony(ctx):
    """Railing on the balcony floor (the floor itself is cut with the first floor band, see structure.band)."""
    cfg = ctx.cfg['details']
    st = ctx.storeys[0]
    y = st.y_band
    colour = ctx.code(cfg['balcony_colour'])
    for (i, j) in sorted(balcony_cells(ctx)):
        if not ctx.canvas.free(i, j, y) and ctx.canvas.free(i, j, y + 1):
            ctx.canvas.put(catalog.ROUND_BRICK_1, colour, 'detail', i + .5, j + .5, y + 1, 0, label='balcony rail')


def chimney(ctx):
    ridge = getattr(ctx, 'ridge', None)
    cfg = ctx.cfg['details']
    if cfg['chimney'] == 'none' or not ridge or 'chimney' not in ridge:
        return
    frame = ridge['frame']
    cu, cv = ridge['chimney']
    colour = ctx.code(cfg['chimney_colour'])
    x, z = frame.point(cu + 1, cv + 1)
    y = ridge['y'] + 1
    for k in range(cfg['chimney_courses']):
        ctx.canvas.put('3003', colour, 'detail', x, z, y + 3 * k, 0, label='chimney')
    ctx.canvas.put('3022', colour, 'detail', x, z, y + 3 * cfg['chimney_courses'], 0, label='chimney cap')
