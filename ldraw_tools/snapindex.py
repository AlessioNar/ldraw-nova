"""Spatial culling for snap previews: inspect only the parts that can touch the snap.

A snap asks which connections a part can make with one fixed part and which of them collide with anything. The
answer only depends on parts near the fixed part, but the preview model used to hold every earlier placement
(and every submodel they reference), so each snap paid for the whole model, and checked up to 64 candidate
poses against all of it. `Neighbourhood.cull` keeps the fixed part, the moving part and whatever overlaps the
region the moving part can reach, so the cost follows the local density instead of the model size.

Everything unknown stays in: a part whose size cannot be established (or whose geometry reports diagnostics,
which would make the inspection incomplete) is never culled, so the result is the one the full model gives.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
from ldraw import Piece

from .common import jsonable, normalized

MARGIN = 40.0      # LDU of slack for connection reach, contact tolerance and the boxes' own looseness


def _identity(piece):
    return np.array(piece.matrix.rows, dtype=float), np.array(jsonable(piece.position), dtype=float)


def _move(low, high, matrix, position):
    """Axis-aligned box of the box (low, high) after `matrix` and `position`; works on stacks of boxes."""
    centre, half = (low + high) / 2, (high - low) / 2
    centre = centre @ matrix.T + position
    half = half @ np.abs(matrix).T
    return centre - half, centre + half


class Neighbourhood:
    """Boxes for the placements of one build, and the culling built on them."""

    def __init__(self, models, parts):
        self.by_name = {normalized(name): model for name, model in models.items()}
        self.parts = parts
        self._local = {}        # normalized reference -> (low, high) in the referenced part's frame, or None
        self._boxes = {}        # id(piece) -> (piece, box in its parent's frame or None)
        self._arrays = {}       # id(model) -> (model, pieces, low, high, known)
        self._culled = 0

    # ---- boxes

    def _opaque(self, piece):
        return normalized(piece.reference).endswith(".dat")

    def local_box(self, piece):
        key = normalized(piece.reference)
        if key in self._local:
            return self._local[key]
        self._local[key] = None             # also stops a (never expected) reference cycle
        model = self.by_name.get(key)
        result = None
        if model is not None:
            _, pieces, low, high, known = self._model_arrays(model, cache=True)
            if len(pieces) and known.all():
                result = (low.min(axis=0), high.max(axis=0))
        else:
            try:
                geometry = self.parts.geometry(piece.part)
            except Exception:
                geometry = None
            if geometry is not None and geometry.bounds is not None and not geometry.diagnostics:
                result = (np.array(jsonable(geometry.bounds.min), dtype=float),
                          np.array(jsonable(geometry.bounds.max), dtype=float))
        self._local[key] = result
        return result

    def box(self, piece):
        """The piece's box in the frame of the model that holds it (None when unknown)."""
        held = self._boxes.get(id(piece))
        if held is not None and held[0] is piece:
            return held[1]
        local = self.local_box(piece)
        box = None
        if local is not None:
            matrix, position = _identity(piece)
            box = _move(local[0], local[1], matrix, position)
        self._boxes[id(piece)] = (piece, box)
        return box

    def _model_arrays(self, model, cache=False):
        held = self._arrays.get(id(model)) if cache else None
        if held is not None and held[0] is model:
            return held
        pieces = [o for o in model.objects if isinstance(o, Piece)]
        low, high = np.zeros((len(pieces), 3)), np.zeros((len(pieces), 3))
        known = np.zeros(len(pieces), dtype=bool)
        for i, piece in enumerate(pieces):
            box = self.box(piece)
            if box is not None:
                low[i], high[i], known[i] = box[0], box[1], True
        held = (model, pieces, low, high, known)
        if cache:
            self._arrays[id(model)] = held
        return held

    # ---- culling

    def region(self, support, moving):
        """Where the moving part can end up when one of its connectors meets one of the fixed part's."""
        fixed, local = self.box(support), self.local_box(moving)
        if fixed is None or local is None:
            return None
        corners = np.array([[x, y, z] for x in (local[0][0], local[1][0]) for y in (local[0][1], local[1][1])
                            for z in (local[0][2], local[1][2])])
        reach = 2 * float(np.linalg.norm(corners, axis=1).max()) + MARGIN
        return fixed[0] - reach, fixed[1] + reach

    def cull(self, objects, support, moving):
        """(objects to inspect, extra submodels): the placements near the snap, others dropped.

        `support` and `moving` are never touched (their leaves are addressed by index). Nested submodels that
        overlap the region are copied with only their nearby leaves, under fresh names.
        """
        region = self.region(support, moving)
        if region is None:
            return list(objects), {}
        extra = {}
        pieces = [o for o in objects if isinstance(o, Piece)]
        low, high = np.zeros((len(pieces), 3)), np.zeros((len(pieces), 3))
        known = np.zeros(len(pieces), dtype=bool)
        for i, piece in enumerate(pieces):
            box = self.box(piece)
            if box is not None:
                low[i], high[i], known[i] = box[0], box[1], True
        hit = ~known | np.all((high + MARGIN >= region[0]) & (low - MARGIN <= region[1]), axis=1)
        verdict = {id(piece): bool(h) for piece, h in zip(pieces, hit)}
        result = []
        for obj in objects:
            if not isinstance(obj, Piece):
                result.append(obj)
            elif obj is support:
                result.append(obj)
            elif verdict[id(obj)]:
                result.append(self._cull_piece(obj, region, np.eye(3), np.zeros(3), extra))
        return result, extra

    def _cull_piece(self, piece, region, matrix, position, extra):
        """The piece itself, or (for a nearby submodel instance) a stand-in that holds only nearby leaves."""
        model = self.by_name.get(normalized(piece.reference))
        if model is None or self._opaque(piece):
            return piece
        own_matrix, own_position = _identity(piece)
        culled = self._cull_model(model, region, matrix @ own_matrix, matrix @ own_position + position, extra)
        if culled is None:       # nothing of it is near: an empty stand-in keeps the placement valid
            culled = self._stand_in(model, [], extra)
        return Piece.place(culled.name, colour=piece.colour, position=piece.position, matrix=piece.matrix)

    def _stand_in(self, model, kept, extra):
        self._culled += 1
        name = f"nova-cull-{self._culled}.ldr"
        stand = replace(model, name=name, objects=kept, submodels={})
        extra[normalized(name)] = stand
        return stand

    def _cull_model(self, model, region, matrix, position, extra):
        _, pieces, low, high, known = self._model_arrays(model, cache=True)
        if not len(pieces):
            return None
        world_low, world_high = _move(low, high, matrix, position)
        hit = ~known | np.all((world_high + MARGIN >= region[0]) & (world_low - MARGIN <= region[1]), axis=1)
        kept = []
        for i in np.nonzero(hit)[0]:
            piece = pieces[i]
            if self.by_name.get(normalized(piece.reference)) is None or self._opaque(piece):
                kept.append(piece)
            else:
                kept.append(self._cull_piece(piece, region, matrix, position, extra))
        return self._stand_in(model, kept, extra) if kept else None

    def needed_submodels(self, model, objects, extra):
        """The submodels the culled preview still refers to, by normalized name."""
        needed, stack = {}, [o.reference for o in objects if isinstance(o, Piece)]
        while stack:
            key = normalized(stack.pop())
            if key in needed:
                continue
            found = extra.get(key) or self.by_name.get(key)
            if found is None or found is model:
                continue
            needed[key] = found
            stack.extend(o.reference for o in found.objects if isinstance(o, Piece))
        return needed
