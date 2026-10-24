"""Layout of storeys and towers, floor bands, and the tops: flat decks, parapets and battlements, tower roofs."""
from __future__ import annotations

from . import catalog, footprint, roofs, tiling
from .context import Storey
from .roofs import _put_cover

DIAG = {'front': lambda s: s[1] == -1, 'back': lambda s: s[1] == 1, 'left': lambda s: s[0] == -1,
        'right': lambda s: s[0] == 1, 'diagonal': lambda s: s[0] == s[1], 'all': lambda s: True}


def tower_rects(cfg, base_cells):
    co = cfg['corners']
    if co['style'] != 'towers':
        return []
    size = 4 if co['tower_shape'] == 'round' else co['tower_size']
    p = min(co['tower_proud'], size - 2)
    out = []
    for (c, s) in footprint.convex_corners(base_cells):
        if not DIAG[co['tower_at']](s):
            continue
        i0 = c[0] - p if s[0] < 0 else c[0] + 1 + p - size
        j0 = c[1] - p if s[1] < 0 else c[1] + 1 + p - size
        out.append((i0, j0, size, size))
    return out


def make_layout(ctx):
    cfg = ctx.cfg
    st_cfg, co = cfg['storeys'], cfg['corners']
    base = footprint.shape_cells(cfg['footprint'])
    rects = tower_rects(cfg, base)
    ctx.tower_rects = rects
    round_t = co['style'] == 'towers' and co['tower_shape'] == 'round'
    tcells = set()
    for (i0, j0, w, d) in rects:
        tcells |= footprint.rect(i0, j0, w, d)
    y = ctx.y_ground
    course0 = 0
    for s in range(st_cfg['count']):
        n = st_cfg['courses']
        if s == st_cfg['count'] - 1 and st_cfg['top_courses']:
            n = st_cfg['top_courses']
        cells = footprint.erode(base, st_cfg['setback'] * s) if st_cfg['setback'] else set(base)
        cells = cells | tcells
        st = Storey(s, 'main', n, y, cells, set() if round_t else tcells & cells, tcells if round_t else set(), course0)
        st.band_h = 2 if st_cfg['floors'] or s == st_cfg['count'] - 1 else 1
        ctx.storeys.append(st)
        y = st.y_top
        course0 += n
    extra = co['tower_extra_courses'] if co['style'] == 'towers' else 0
    if extra:
        st = Storey(len(ctx.storeys), 'tower', extra, y, set(tcells), set() if round_t else set(tcells),
                    tcells if round_t else set(), course0)
        st.band_h = 2
        ctx.storeys.append(st)
    for st in ctx.storeys:
        st.walls = footprint.Walls(st.cells, ctx.t)
    ctx.top_main = ctx.storeys[st_cfg['count'] - 1].y_band
    return ctx.storeys


def band(ctx, st):
    """Floor band on top of the storey: trim ring, interior floor, optional cornice projection, round tower plates."""
    cfg = ctx.cfg
    s_cfg = cfg['storeys']
    cells = st.cells - st.blocked
    mass = st.walls.mass - st.blocked
    y = st.y_band
    main_top = st.kind == 'main' and st.index == s_cfg['count'] - 1
    tower_top = st.kind == 'tower' or (main_top and False)
    trim = ctx.code(s_cfg['band_colour'])
    roof = ctx.code(cfg['roof']['colour'])
    tower = ctx.code(cfg['corners']['tower_colour'])
    inside = cells - mass
    # One slab: plates straddle ring and interior so the floor is held by the walls (one colour per plate).
    slab = set(mass)
    if s_cfg['floors'] or main_top or st.kind == 'tower':
        slab |= inside
    groups = {}
    for c in slab:
        if st.kind == 'tower' or c in st.tower_cells:
            key = ('tower', tower)
        elif main_top and cfg['roof']['style'] == 'flat':
            key = ('roof', roof)
        else:
            key = ('trim', trim)
        groups.setdefault(key, set()).add(c)
    if st.index == 0:
        from .details import balcony_cells
        extra = {c for c in balcony_cells(ctx) if ctx.canvas.free(c[0], c[1], y)}
        if extra:
            groups.setdefault(('detail', ctx.code(cfg['details']['balcony_colour'])), set()).update(extra | {(c[0], c[1] + 1) for c in extra if (c[0], c[1] + 1) in mass})
            groups[('trim', trim)] = groups.get(('trim', trim), set()) - groups[('detail', ctx.code(cfg['details']['balcony_colour']))]
    p = s_cfg['band_projection']
    if p:
        ring = set()
        for (i, j) in cells | mass:
            for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                n = (i + a, j + b)
                if n not in cells and n not in st.blocked and ctx.canvas.free(n[0], n[1], y):
                    ring.add(n)
        key = ('roof', roof) if main_top and cfg['roof']['style'] == 'flat' else ('trim', trim)
        groups.setdefault(key, set()).update(ring)       # cut together with the slab so plates straddle wall and cornice
    layered = st.band_h == 2
    for (role, colour), cs in sorted(groups.items()):
        cs = _free(ctx, cs, y)
        if layered:
            top = {c for c in cs if c in cells}
            lower, upper_cover = tiling.two_layers(cs, mass & cs, top)
            for (code, i, j, w, d) in lower:
                _put_cover(ctx, code, colour, role, i, j, w, d, y, f'band s{st.index}')
            for (code, i, j, w, d) in upper_cover:
                if all(ctx.canvas.free(i + a, j + b, y + 1) for a in range(w) for b in range(d)):
                    _put_cover(ctx, code, colour, role, i, j, w, d, y + 1, f'floor s{st.index}')
        else:
            for (code, i, j, w, d) in tiling.rect_cover(cs, 'plate'):
                _put_cover(ctx, code, colour, role, i, j, w, d, y, f'band s{st.index}')
    for (i0, j0, w, d) in ctx.tower_rects if st.blocked else []:
        for k in range(st.band_h):
            ctx.canvas.put(catalog.ROUND_PLATE_4, ctx.code(cfg['corners']['tower_colour']), 'tower', i0 + 2, j0 + 2, y + k, 0,
                           label='tower band')


def _free(ctx, cells, y):
    return {c for c in cells if ctx.canvas.free(c[0], c[1], y)}


def _join_ring(ctx, ring, mass, y):
    """A cornice is cut together with the wall cells beside it so its plates straddle the wall and hold on."""
    near = set()
    for (i, j) in ring:
        for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + a, j + b)
            if n in mass and ctx.canvas.free(n[0], n[1], y):
                near.add(n)
    return ring | near


def round_towers(ctx, st):
    """Stack 4x4 round bricks in each tower rect for the storey's courses."""
    if not st.blocked:
        return
    colour = ctx.code(ctx.cfg['corners']['tower_colour'])
    for (i0, j0, w, d) in ctx.tower_rects:
        for c in range(st.courses):
            ctx.canvas.put(catalog.ROUND_BRICK_4, colour, 'tower', i0 + 2, j0 + 2, st.y_floor + 3 * c, 0,
                           label=f'round tower c{st.course0 + c}')


def merlons(walls, outer, m, g):
    """Cells of `outer` (outer wall cells) that carry a merlon; corners always do."""
    on = set()
    kind = walls.kind
    for c in outer:
        if kind.get(c) == 'corner':
            on.add(c)
    rows, cols = {}, {}
    for c in sorted(outer, key=lambda c: (c[1], c[0])):
        if kind.get(c) in ('h', 'corner'):
            rows.setdefault(c[1], []).append(c[0])
    for c in sorted(outer):
        if kind.get(c) in ('v', 'corner'):
            cols.setdefault(c[0], []).append(c[1])

    def mark(runs, make):
        for key, poss in runs.items():
            run = [poss[0]]
            for p in poss[1:] + [None]:
                if p is not None and p == run[-1] + 1:
                    run.append(p)
                    continue
                L = len(run)
                for k, q in enumerate(run):
                    if min(k, L - 1 - k) % (m + g) < m:
                        on.add(make(key, q))
                run = [p] if p is not None else []
    mark(rows, lambda key, q: (q, key))
    mark(cols, lambda key, q: (key, q))
    return {c for c in on if c in outer}


def parapet(ctx, st, cells, y, colour, role, courses, style, m, g):
    """Bricks on the outer wall cells in `cells`: a solid parapet or alternating merlons and gaps."""
    walls = st.walls
    outer = {c for c in cells if walls.dist.get(c) == 1 and c in walls.mass}
    chosen = outer if style == 'plain' else merlons(walls, outer, m, g)
    for k in range(courses):
        yk = y + 3 * k
        done = set()
        for axis in ('h', 'v'):
            lines = {}
            for c in sorted(chosen):
                a = walls.axis(c, 0) if walls.kind[c] != 'corner' else 'h'
                if a != axis or c in done:
                    continue
                key, pos = (c[1], c[0]) if axis == 'h' else (c[0], c[1])
                lines.setdefault(key, []).append(pos)
            for key, poss in sorted(lines.items()):
                run = [poss[0]]
                for p in poss[1:] + [None]:
                    if p is not None and p == run[-1] + 1:
                        run.append(p)
                        continue
                    pos = run[0]
                    for length in tiling.tile_run(len(run), run[0], set()):
                        if axis == 'h':
                            ctx.canvas.put(catalog.BRICK[length], colour, role, pos + length / 2, key + .5, yk, 0, label='parapet')
                        else:
                            ctx.canvas.put(catalog.BRICK[length], colour, role, key + .5, pos + length / 2, yk, 90, label='parapet')
                        pos += length
                    run = [p] if p is not None else []
    return outer


def main_top(ctx):
    cfg = ctx.cfg
    st = ctx.storeys[cfg['storeys']['count'] - 1]
    y = st.y_top
    style = cfg['roof']['style']
    cells = st.cells - st.blocked
    x0, x1, z0, z1 = footprint.bbox(st.cells - st.tower_cells - st.blocked) if style in ('gable', 'hip') else (0, 0, 0, 0)
    if style == 'gable':
        roofs.gable(ctx, (x0, z0, x1 - x0, z1 - z0), y, st)
    elif style == 'hip':
        roofs.hip(ctx, (x0, z0, x1 - x0, z1 - z0), y, ctx.code(cfg['roof']['colour']), 'roof', cfg['roof']['tiers'])
    elif style == 'flat' and cfg['roof']['parapet'] != 'none':
        r = cfg['roof']
        parapet(ctx, st, cells - st.tower_cells, y, ctx.code(r['crenel_colour']), 'wall', r['parapet_courses'],
                r['parapet'], r['merlon'], r['gap'])


def tower_tops(ctx):
    co = ctx.cfg['corners']
    if co['style'] != 'towers' or not ctx.tower_rects:
        return
    last = ctx.storeys[-1]
    y = last.y_top
    colour = ctx.code(co['tower_colour'])
    roof = ctx.code(co['tower_roof_colour'])
    if co['tower_shape'] == 'round':
        for (i0, j0, w, d) in ctx.tower_rects:
            if co['tower_roof'] == 'cone':
                ctx.canvas.put(catalog.CONE_4, roof, 'roof', i0 + 2, j0 + 2, y, 0, label='tower cone')
            else:
                ctx.canvas.put(catalog.ROUND_PLATE_4, colour, 'tower', i0 + 2, j0 + 2, y, 0, label='tower cap')
                for (a, b) in ((1, 0), (2, 3), (0, 2), (3, 1)):
                    ctx.canvas.put(catalog.ROUND_BRICK_1, colour, 'tower', i0 + a + .5, j0 + b + .5, y + 1, 0, label='tower merlon')
        return
    if co['tower_roof'] == 'cone':
        for (i0, j0, w, d) in ctx.tower_rects:
            roofs.hip(ctx, (i0, j0, w, d), y, roof, 'roof', 0, 'tower pyramid')
    else:
        r = ctx.cfg['roof']
        parapet(ctx, last, last.tower_cells - last.blocked, y, colour, 'tower', 1, 'battlement', r['merlon'], r['gap'])
