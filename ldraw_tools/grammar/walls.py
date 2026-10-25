"""Wall courses: cells -> running-bond bricks, with colour by style, corners that interlock, openings left open."""
from __future__ import annotations

from . import catalog, parts


def wall_colours(ctx, st, course, cell, axis, corners, carved):
    """(colour code, role) for a mass cell in this course (before the stone per-brick mix)."""
    w, co = ctx.cfg['walls'], ctx.cfg['corners']
    gc = st.course0 + course
    in_tower = cell in st.tower_cells
    base_role = 'tower' if in_tower else 'wall'
    if st.index == 0 and course == 0 and w['plinth']:
        return ctx.code(w['plinth_colour']), 'trim'
    if w['accent_every'] and gc % w['accent_every'] == w['accent_every'] - 1:
        return ctx.code(w['accent_colour']), 'trim'
    if cell in st.shutters.get(course, ()):
        return ctx.code(ctx.cfg['openings']['shutter_colour']), 'detail'
    if cell in st.keystones.get(course, ()):
        return ctx.code(ctx.cfg['openings']['keystone_colour']), 'trim'
    main = ctx.code(ctx.cfg['corners']['tower_colour'] if in_tower else w['colour'])
    if co['style'] == 'pillars' and cell in corners and not in_tower:
        return ctx.code(co['pillar_colour']), 'trim'
    if w['style'] == 'timber' and not in_tower:
        post = cell in corners
        post = post or (axis == 'h' and cell[0] % 4 == 0) or (axis == 'v' and cell[1] % 4 == 0)
        if not post:
            for (a, b) in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if (cell[0] + a, cell[1] + b) in carved:
                    post = True
        if post:
            return ctx.code(w['timber_colour']), 'trim'
    return main, base_role


def tile_course(ctx, st, course, y0, joints, override=None):
    """Place the bricks of one wall course. joints: dict with 'h' and 'v' maps of the previous course's joints."""
    walls = st.walls
    carved = st.carve.get(course, set())
    gc = st.course0 + course
    corners = walls.corner_cells()
    cells = [c for c in sorted(walls.mass) if c not in carved and c not in st.blocked]
    taken = technic_bricks(ctx, st, course, y0, set(cells))
    cells = [c for c in cells if c not in taken]
    entries = {}
    for c in cells:
        axis = walls.axis(c, gc)
        colour, role = wall_colours(ctx, st, course, c, axis, corners, carved)
        if override:
            colour, role = override(c, course, colour, role)
        entries[c] = (axis, colour, role)
    cur = {'h': {}, 'v': {}}
    for axis in ('h', 'v'):
        lines = {}
        for c, (a, colour, role) in entries.items():
            if a == axis:
                key, pos = (c[1], c[0]) if axis == 'h' else (c[0], c[1])
                lines.setdefault(key, {})[pos] = (colour, role)
        for key in sorted(lines):
            positions = sorted(lines[key])
            run = [positions[0]]
            for p in positions[1:] + [None]:
                if p is not None and p == run[-1] + 1 and lines[key][p] == lines[key][run[0]]:
                    run.append(p)
                    continue
                colour, role = lines[key][run[0]]
                avoid = set(joints[axis].get(key, ())) | cur[axis].get(key - 1, set())
                lengths = parts.fit_run(ctx, len(run), run[0], avoid, catalog.BRICK, colour)
                pos = run[0]
                for length in lengths:
                    place_brick(ctx, st, axis, key, pos, length, colour, role, y0, course)
                    cur[axis].setdefault(key, set()).update({pos, pos + length})
                    pos += length
                if p is not None:
                    run = [p]
    joints['h'], joints['v'] = cur['h'], cur['v']


def technic_bricks(ctx, st, course, y0, free):
    """Technic bricks with a pin hole in the side walls at the standard module positions (the second course from the top of every storey)."""
    if not ctx.cfg['details']['connectors'] or course != st.courses - 2 or st.kind != 'main':
        return set()
    x0, x1, z0, z1 = footprint_bbox(st)
    depth = z1 - z0
    taken = set()
    for j in (z0 + depth // 4, z0 + 3 * depth // 4 - 1):
        for i in (x0, x1 - 1):
            if ctx.cfg['base']['corner'] == ('left' if i == x0 else 'right'):
                continue                      # a street side is not bolted to a neighbour
            pair = {(i, j), (i, j + 1)}
            if pair <= free and all(st.walls.axis(c, st.course0 + course) == 'v' for c in pair):
                ctx.canvas.put(catalog.TECHNIC_BRICK, ctx.code(72), 'structure', i + .5, j + 1, y0, 90, label='module connector')
                taken |= pair
    return taken


def footprint_bbox(st):
    from .footprint import bbox
    return bbox(st.cells - st.blocked)


def place_brick(ctx, st, axis, key, pos, length, colour, role, y0, course):
    w = ctx.cfg['walls']
    if w['style'] == 'stone' and role == 'wall' and (pos * 5 + key * 11 + (st.course0 + course) * 3 + length) % 7 < 2:
        colour = ctx.code(w['alt_colour'])
    code = catalog.BRICK[length]
    if axis == 'h':
        ctx.canvas.put(code, colour, role, pos + length / 2, key + .5, y0, 0, label=f'wall s{st.index} c{course}')
    else:
        ctx.canvas.put(code, colour, role, key + .5, pos + length / 2, y0, 90, label=f'wall s{st.index} c{course}')


def build(ctx, st, override=None):
    joints = {'h': {}, 'v': {}}
    for course in range(st.courses):
        tile_course(ctx, st, course, st.y_floor + 3 * course, joints, override)
