"""Course-by-course maps of a built building, to check structure without a renderer.

ascii_course: top view of one plate level (letter = role of the part occupying each cell).
ascii_front / ascii_side: elevations (the nearest part seen from the front / the left).
svg_sheet: front elevation, side elevation and a column of storey plans as one SVG with a role colour key.
"""
from __future__ import annotations

LETTER = {'wall': 'W', 'window': 'o', 'door': 'D', 'glass': 'g', 'roof': 'R', 'base': '.', 'tower': 'T', 'detail': 'd',
          'trim': 't', 'structure': 's'}
FILL = {'wall': '#c9a66b', 'window': '#6aa7d8', 'door': '#7a4a2a', 'glass': '#bfe3ff', 'roof': '#a52a2a', 'base': '#4f9d4f',
        'tower': '#9a9a9a', 'detail': '#d4a017', 'trim': '#f0f0f0', 'structure': '#bbbbbb'}


def _grid(canvas):
    cells = {}
    for it in canvas.items:
        if it.host is not None:
            continue
        for (i, j) in it.cells:
            for y in range(it.y0, it.y0 + it.h):
                cells[(i, j, y)] = it.role
    return cells


def ascii_course(canvas, y):
    cells = _grid(canvas)
    x0, x1, z0, z1 = canvas.bounds()
    rows = []
    for j in range(z0, z1):
        rows.append(''.join(LETTER.get(cells.get((i, j, y)), ' ') if (i, j, y) in cells else ' ' for i in range(x0, x1)))
    return '\n'.join(rows)


def _elevation(canvas, side):
    cells = _grid(canvas)
    x0, x1, z0, z1 = canvas.bounds()
    ymax = max(k[2] for k in cells) + 1
    out = []
    for y in range(ymax - 1, -1, -1):
        row = []
        span = range(x0, x1) if side == 'front' else range(z0, z1)
        for a in span:
            found = None
            depth = range(z0, z1) if side == 'front' else range(x0, x1)
            for b in depth:
                key = (a, b, y) if side == 'front' else (b, a, y)
                if key in cells:
                    found = cells[key]
                    break
            row.append(LETTER.get(found, ' ') if found else ' ')
        out.append(''.join(row))
    return '\n'.join(out)


def ascii_front(canvas):
    return _elevation(canvas, 'front')


def ascii_side(canvas):
    return _elevation(canvas, 'side')


def storey_levels(ctx_or_result):
    return [st.y_floor for st in ctx_or_result]


def svg_sheet(canvas, title='building'):
    cells = _grid(canvas)
    x0, x1, z0, z1 = canvas.bounds()
    ymax = max(k[2] for k in cells) + 1
    u = 8
    pad = 16
    w_front, w_side = (x1 - x0) * u, (z1 - z0) * u
    width = pad * 3 + w_front + w_side + 140
    height = pad * 2 + ymax * u * 0.8 + 24
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" font-family="sans-serif" font-size="11">',
             f'<rect width="100%" height="100%" fill="#ffffff"/><text x="{pad}" y="14" font-weight="bold">{title}: front and left elevation (1 stud = {u}px, 1 plate = {u * .8:.1f}px)</text>']
    for k, side in enumerate(('front', 'side')):
        ox = pad + (0 if side == 'front' else w_front + pad)
        span = range(x0, x1) if side == 'front' else range(z0, z1)
        depth = range(z0, z1) if side == 'front' else range(x0, x1)
        for a in span:
            for y in range(ymax):
                role = None
                for b in depth:
                    key = (a, b, y) if side == 'front' else (b, a, y)
                    if key in cells:
                        role = cells[key]
                        break
                if role:
                    px = ox + (a - (x0 if side == 'front' else z0)) * u
                    py = 24 + (ymax - 1 - y) * u * .8
                    parts.append(f'<rect x="{px}" y="{py:.1f}" width="{u}" height="{u * .8:.1f}" fill="{FILL[role]}" stroke="#00000022" stroke-width=".5"/>')
    kx = pad * 3 + w_front + w_side
    for n, (role, colour) in enumerate(FILL.items()):
        parts.append(f'<rect x="{kx}" y="{30 + n * 18}" width="12" height="12" fill="{colour}" stroke="#555"/><text x="{kx + 18}" y="{40 + n * 18}">{role}</text>')
    parts.append('</svg>')
    return '\n'.join(parts)


def course_report(canvas, levels):
    """Text: front elevation plus the top view at each plate level in `levels`."""
    out = ['FRONT ELEVATION (top = roof)', ascii_front(canvas), '']
    for y in levels:
        out += [f'PLAN at plate {y}', ascii_course(canvas, y), '']
    return '\n'.join(out)
