"""Add-ons: base plate and path, porch, balcony, chimney."""
from __future__ import annotations

from . import catalog, footprint, parts
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
    if cfg['kind'] == 'modular':               # the 32x32 module: sidewalk in front, the building behind it
        ctx.base_rect = (x0, z0 - cfg['sidewalk'], 32, 32)
    colour = ctx.code(cfg['colour'])
    i0, j0, w, d = ctx.base_rect
    # Two layers of plate strips laid across each other, each strip's joints staggered against the last.
    for layer, (length, breadth, yaw) in enumerate(((w, d, 0), (d, w, 90))):
        prev, pos = set(), 0
        while pos < breadth:
            depth = 2 if breadth - pos >= 2 else 1
            lengths = parts.fit_run(ctx, length, 0, prev, catalog.PLATE2 if depth == 2 else catalog.PLATE, colour,
                                    (8, 6, 4, 3, 2) if depth == 2 else (8, 6, 4, 3, 2, 1))
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


def sidewalk(ctx):
    """Modular: tiles across the whole front of the module, a kerb row, optional lamps on plain studs."""
    cfg = ctx.cfg['base']
    bx0, bz0, bw, bd = ctx.base_rect
    depth = cfg['sidewalk']
    if not depth:
        return
    lamps = []
    if cfg['lamps']:
        lamps = [(bx0 + 3, bz0 + depth - 2), (bx0 + bw - 4, bz0 + depth - 2)]
    cells = {(i, j) for i in range(bx0, bx0 + bw) for j in range(bz0, bz0 + depth)} - set(lamps)
    kerb = {c for c in cells if c[1] == bz0}
    for cells_, colour in ((cells - kerb, ctx.code(cfg['path_colour'])), (kerb, ctx.code(72))):
        free = {c for c in cells_ if ctx.canvas.free(c[0], c[1], ctx.y_ground)}
        for (code, i, j, w, d) in parts.cover(ctx, free, 'tile', colour):
            _put_cover(ctx, code, colour, 'base', i, j, w, d, ctx.y_ground, 'sidewalk')
    for (i, j) in lamps:
        for k in range(4):
            ctx.canvas.put(catalog.ROUND_BRICK_1, ctx.code(0), 'detail', i + .5, j + .5, ctx.y_ground + 3 * k, 0, label='street lamp')
        ctx.canvas.put(catalog.ROUND_BRICK_1, ctx.code('trans yellow'), 'detail', i + .5, j + .5, ctx.y_ground + 12, 0, label='street lamp')


def path(ctx):
    cfg = ctx.cfg['base']
    if cfg['kind'] == 'modular':
        sidewalk(ctx)
        return
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
    for (code, i, j, w, d) in parts.cover(ctx, cells, 'tile', colour):
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
    for (code, i, j, w, d) in parts.cover(ctx, cells, 'plate', colour):
        _put_cover(ctx, code, colour, 'detail', i, j, w, d, y, 'porch floor')
    for (pi, pj) in ((u0 - 1, line - 2), (u0 + 4, line - 2)):
        for k in range(5):
            ctx.canvas.put(catalog.BRICK[1], colour, 'detail', pi + .5, pj + .5, y + 1 + 3 * k, 0, label='porch pillar')
        ctx.canvas.put(catalog.PLATE[1], colour, 'detail', pi + .5, pj + .5, y + 16, 0, label='porch pillar')
        ctx.canvas.put(catalog.PLATE[1], colour, 'detail', pi + .5, pj + .5, y + 17, 0, label='porch pillar')
    top = footprint.rect(u0 - 1, line - 2, 6, 2)
    for (code, i, j, w, d) in parts.cover(ctx, top, 'plate', roof):
        _put_cover(ctx, code, roof, 'detail', i, j, w, d, y + 18, 'porch canopy')
    for (code, i, j, w, d) in parts.cover(ctx, top, 'tile', roof):
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


def fence(ctx):
    """Picket fence on the outer ring of the base plate: 1x1 posts two studs apart under 1x3 rails, gaps where
    something (the path) already stands. Everything stands on the base plate, so it is connected."""
    cfg = ctx.cfg['details']
    if not cfg['fence'] or ctx.base_rect is None or ctx.cfg['base']['kind'] != 'plate':
        return
    colour = ctx.code(cfg['fence_colour'])
    bx0, bz0, bw, bd = ctx.base_rect
    y = ctx.y_ground
    lines = [[(i, bz0) for i in range(bx0, bx0 + bw)], [(i, bz0 + bd - 1) for i in range(bx0, bx0 + bw)],
             [(bx0, j) for j in range(bz0 + 1, bz0 + bd - 1)], [(bx0 + bw - 1, j) for j in range(bz0 + 1, bz0 + bd - 1)]]
    for line in lines:
        run = []
        for cell in line + [None]:
            if cell is not None and ctx.canvas.all_free([cell], y, 4):
                run.append(cell)
                continue
            _fence_run(ctx, run, colour, y)
            run = []


def _fence_run(ctx, run, colour, y):
    pos = 0
    while pos < len(run):
        n = min(3, len(run) - pos)
        posts = (pos, pos + 2) if n == 3 else tuple(range(pos, pos + n))
        for p in posts:
            ctx.canvas.put(catalog.BRICK[1], colour, 'detail', run[p][0] + .5, run[p][1] + .5, y, 0, label='fence post')
        first, last = run[pos], run[pos + n - 1]
        if n > 1:
            horizontal = first[1] == last[1]
            cx, cz = (first[0] + last[0]) / 2 + .5, (first[1] + last[1]) / 2 + .5
            ctx.canvas.put(catalog.PLATE[n], colour, 'detail', cx, cz, y + 3, 0 if horizontal else 90, label='fence rail')
        else:
            ctx.canvas.put(catalog.PLATE[1], colour, 'detail', first[0] + .5, first[1] + .5, y + 3, 0, label='fence rail')
        pos += n
