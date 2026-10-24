"""Wall courses: cells -> running-bond bricks, with colour by style, corners that interlock, openings left open."""
from __future__ import annotations

from . import catalog, tiling
from .canvas import Canvas


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
    canvas: Canvas = ctx.canvas
    walls = st.walls
    carved = st.carve.get(course, set())
    gc = st.course0 + course
    corners = walls.corner_cells()
    cells = [c for c in sorted(walls.mass) if c not in carved and c not in st.blocked]
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
                lengths = choose_lengths(ctx, len(run), run[0], avoid, colour)
                pos = run[0]
                for length in lengths:
                    place_brick(ctx, st, axis, key, pos, length, colour, role, y0, course)
                    cur[axis].setdefault(key, set()).update({pos, pos + length})
                    pos += length
                if p is not None:
                    run = [p]
    joints['h'], joints['v'] = cur['h'], cur['v']


def choose_lengths(ctx, n, start, avoid, colour):
    inv = ctx.inv
    allowed = tiling.BRICK_LENGTHS
    owned = None
    if inv is not None:
        owned_set = {n_ for n_ in tiling.BRICK_LENGTHS if inv.left(catalog.BRICK[n_], colour) > 0}
        if inv.mode == 'only' and owned_set:
            allowed = tuple(x for x in tiling.BRICK_LENGTHS if x in owned_set)
            try:
                return tiling.tile_run(n, start, avoid, allowed)
            except ValueError:
                allowed = tiling.BRICK_LENGTHS
        owned = (lambda length: 0.0 if length in owned_set else 1.8)
    return tiling.tile_run(n, start, avoid, allowed, owned=owned)


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
