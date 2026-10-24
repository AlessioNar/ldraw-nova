"""A numpy `transformed_bounds` for pyldraw's inspection (pinned 1.7.x).

`ldraw.inspection.transformed_bounds` moves every vertex of a part's expanded geometry through a Python
`position + matrix * point` (a numpy call per vertex) once per inspected placement: tens of milliseconds for one
brick, so inspecting a few hundred placements took seconds. The same bounds come from one array product against
the part's vertices, which are converted once per part and kept.

Results agree with the original to floating-point rounding (different summation order inside numpy); every use
of these bounds has tolerances orders of magnitude larger. The original is kept when the signature is not the
one this was written against.
"""
from __future__ import annotations

import inspect

import numpy as np
from ldraw import Vector
from ldraw import inspection
from ldraw.part_geometry_types import BoundingBox

_ARRAYS: dict[int, tuple[object, np.ndarray]] = {}
MAX_PARTS_KEPT = 4096


def _array(points):
    held = _ARRAYS.get(id(points))
    if held is not None and held[0] is points:
        return held[1]
    array = np.array([(p.x, p.y, p.z) for p in points], dtype=float).reshape(-1, 3)
    if len(_ARRAYS) >= MAX_PARTS_KEPT:
        _ARRAYS.clear()
    if isinstance(points, tuple):          # only an immutable sequence can safely be remembered by identity
        _ARRAYS[id(points)] = (points, array)
    return array


def vertices(points):
    """A part's expanded vertices as an (n, 3) float array, converted once per part."""
    return _array(points if isinstance(points, tuple) else tuple(points))


def transformed_bounds(points, *, position, matrix):
    """Exact world AABB after transforming expanded points (numpy version of the pyldraw function)."""
    array = _array(points if isinstance(points, tuple) else tuple(points))
    if not len(array):
        return None
    world = array @ np.array(matrix.rows, dtype=float).T + np.array((position.x, position.y, position.z))
    low, high = world.min(axis=0), world.max(axis=0)
    return BoundingBox(Vector(float(low[0]), float(low[1]), float(low[2])),
                       Vector(float(high[0]), float(high[1]), float(high[2])))


_ORIGINAL = inspection.transformed_bounds


def uninstall():
    """Back to pyldraw's own function (the tests compare against it)."""
    inspection.transformed_bounds = _ORIGINAL


def install():
    original = _ORIGINAL
    if list(inspect.signature(original).parameters) != ["points", "position", "matrix"]:
        return
    transformed_bounds.nova_fast = True
    inspection.transformed_bounds = transformed_bounds
