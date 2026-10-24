"""The editor's snap, answered without inspecting the neighbourhood: "which connection is nearest the drop?"

`connectivity.snap_report` ranks up to 100 candidate poses, and for each one inspects every nearby placement and
checks every pair of them. The editor only wants the candidate closest to where the part was dropped, and in the
ordinary case (a free, collision-free, confirmed connection) that candidate is known after checking a handful:

* candidates are generated and filtered exactly as `snap_report` does (same connectors, same occupancy rule, same
  de-duplication, the same cap of 100 distinct poses), which needs only the moving part, the fixed part and the
  parts touching the fixed part;
* they are then examined in order of distance from the drop, each with the checks `snap_report` makes, but
  against the parts that overlap its own pose (placements are inspected one by one, on demand), and with contact
  searches limited to the pairs that are read (the pair of selected endpoints and the pairs a body overlap makes);
* when the first accepted candidate in that order has no collision and a confirmed contact, no unexamined
  candidate can rank above it (its rank key cannot be beaten and everything farther from the drop comes later),
  so after the candidates that tie with it in distance are examined the answer is final. Otherwise every
  candidate is examined, which is the full algorithm.

The result has the shape `snap_report` returns (a ranked `candidates` list), and the caller picks from it as
before. Anything this does not cover (a submodel or Technic part involved, incomplete geometry, an unknown
connector option) returns None and the caller runs `snap_report`.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import chain

import numpy as np
from ldraw import Model
from ldraw.inspection import inspect_model

from .collision import collision_pairs
from .common import jsonable
from .connection_adapter import connection_contacts, query_frames
from .connectivity import moved_inspection, pose, transform_dict
from .document import physical_context
from .geometry import profiles
from .snapindex import _move
from .technic import candidates as technic_candidates, curate, managed

NEIGHBOUR_MARGIN = 10.0     # LDU: parts farther than this from a pose cannot touch or collide with it
TIE = 1e-6                  # squared LDU: candidates this close in distance from the drop are examined together
MAX_CANDIDATES = 100        # the same cap snap_report applies
LIMIT = 64                  # the same ranked-list length the editor asks snap_report for
_NOTHING = Model(name="")


class Fallback(Exception):
    """The fast answer cannot be proven the same as the full one here."""


class Scene:
    """The occurrences of a preview model, inspected one at a time when they are needed."""

    def __init__(self, preview, parts, instance_limit, hood):
        model, self.parts = physical_context(preview, parts)
        self.occurrences = []
        for occurrence in model.iter_occurrences(include_steps=True):
            self.occurrences.append(occurrence)
            if len(self.occurrences) > instance_limit:
                raise ValueError(f'Connection budget exceeds {instance_limit} occurrences; select a section')
        count = len(self.occurrences)
        self.low, self.high = np.zeros((count, 3)), np.zeros((count, 3))
        self.known = np.zeros(count, dtype=bool)
        for i, occurrence in enumerate(self.occurrences):
            local = hood.local_box(occurrence.piece)
            if local is not None:
                matrix = np.array(occurrence.matrix.rows, dtype=float)
                position = np.array(jsonable(occurrence.position), dtype=float)
                self.low[i], self.high[i] = _move(local[0], local[1], matrix, position)
                self.known[i] = True
        self.items = {}
        self.template = None

    def get(self, index):
        """The curated geometry of occurrence `index`, numbered as in the whole model; Fallback if incomplete."""
        if index not in self.items:
            inspection = inspect_model(_NOTHING, self.parts, occurrences=[self.occurrences[index]])
            if inspection.skipped_geometry or inspection.diagnostics or not inspection.occurrences:
                raise Fallback("incomplete geometry")
            item = curate(inspection).occurrences[0]
            self.items[index] = replace(item, attribution=replace(item.attribution, index=index))
            if self.template is None:
                self.template = inspection
        return self.items[index]

    def near(self, bounds, margin=NEIGHBOUR_MARGIN):
        """Indices of the occurrences that may lie within `margin` of `bounds` (everything of unknown size too)."""
        low = np.array(jsonable(bounds.min)) - margin
        high = np.array(jsonable(bounds.max)) + margin
        hit = ~self.known | np.all((self.high >= low) & (self.low <= high), axis=1)
        return [int(i) for i in np.nonzero(hit)[0]]

    def inspection(self, items):
        return replace(self.template, occurrences=tuple(sorted(items, key=lambda o: o.index)))


def nearest(preview, parts, moving, support, near, options, instance_limit, hood):
    """snap_report-shaped result for the editor's nearest-to-the-drop question, or None for the full algorithm."""
    if (options.get("moving_feature") or options.get("fixed_feature") or options.get("moving_leaf", 0)
            or options.get("fixed_leaf", 0) or options.get("candidate") is not None):
        return None
    try:
        return _nearest(preview, parts, moving, support, near, instance_limit, hood)
    except Fallback:
        return None


def _nearest(preview, parts, moving, support, near, instance_limit, hood):
    scene = Scene(preview, parts, instance_limit, hood)
    moving_at = [i for i, o in enumerate(scene.occurrences) if o.path[0].piece is moving]
    fixed_at = [i for i, o in enumerate(scene.occurrences) if o.path[0].piece is support]
    if len(moving_at) != 1 or len(fixed_at) != 1:
        raise Fallback("a submodel is involved")
    m, f = moving_at[0], fixed_at[0]
    subject, fixed = scene.get(m), scene.get(f)
    for i in np.nonzero(~scene.known)[0]:       # parts of unknown size are always checked: they may be what is broken
        scene.get(int(i))
    if any(managed(feature) for feature in (*subject.connections, *fixed.connections)):
        raise Fallback("Technic interfaces")
    if len(subject.connections) * len(fixed.connections) > 100000:
        raise Fallback("too many connector pairs")

    # What touches the fixed part decides which of its connectors are occupied.
    around = [scene.get(i) for i in scene.near(fixed.bounds) if i not in (m, f)]
    occupied = set()
    for contact in connection_contacts(scene.inspection([fixed, *around])):
        occupied.update(((contact.first_occurrence.index, contact.first.feature_id),
                         (contact.second_occurrence.index, contact.second.feature_id)))

    selection = scene.inspection([subject, fixed])
    ordinary = replace(selection, occurrences=tuple(replace(o, connections=tuple(
        feature for feature in o.connections if not managed(feature))) for o in selection.occurrences))
    raw = chain(query_frames(ordinary, snapping=True).snap_candidates(m, fixed=f),
                technic_candidates(selection, m, f))
    old_world = pose(subject.occurrence.position, subject.occurrence.matrix)
    target_world = pose(moving.position, moving.matrix)
    pending, seen = [], set()
    for candidate in raw:
        if candidate.fixed_occurrence.index == m:
            continue
        if not managed(candidate.moving) and ((m, candidate.moving.feature_id) in occupied or
                                              (candidate.fixed_occurrence.index, candidate.fixed.feature_id) in occupied):
            continue
        new_world = pose(candidate.transform.position, candidate.transform.matrix)
        delta = new_world @ np.linalg.inv(old_world)
        key = tuple(delta.round(7).flat)
        if key in seen:
            continue
        seen.add(key)
        if len(pending) >= MAX_CANDIDATES:
            break
        local = np.linalg.inv(np.eye(4)) @ delta @ target_world
        distance_from_drop = sum((a - b) ** 2 for a, b in zip(local[:3, 3].tolist(), near))
        pending.append((distance_from_drop, candidate, new_world, delta, local))

    regular = profiles()

    def examine(candidate, new_world, delta, local):
        """The candidate's entry in snap_report's list, or None when snap_report would drop it."""
        moved = moved_inspection(scene.inspection([subject]), {m}, delta).occurrences[0]
        others = [scene.get(i) for i in scene.near(moved.bounds) if i not in (m, f)]
        transformed = scene.inspection([moved, fixed, *others])
        endpoints = {m: candidate.moving.feature_id, f: candidate.fixed.feature_id}
        mating = connection_contacts(replace(transformed, occurrences=tuple(
            replace(o, connections=tuple(feature for feature in o.connections if feature.feature_id == endpoints[o.index]))
            for o in transformed.occurrences if o.index in endpoints)))
        if not mating:
            return None
        pairs = list(collision_pairs(transformed, regular, moving={m}))
        blocked = [p for p in pairs if p['status'] == 'rectangular_body_overlap']
        partners = {i for p in pairs for i in p['instances']} - {m}
        # Only pairs a body overlap makes are asked whether they are connected, so only those are searched.
        found = connection_contacts(replace(transformed, occurrences=tuple(
            o for o in transformed.occurrences if o.index == m or o.index in partners))) if partners else ()
        connected_pairs = {tuple(sorted((c.first_occurrence.index, c.second_occurrence.index))) for c in found}
        for pair in pairs:
            pair['connection_present'] = tuple(sorted(pair['instances'])) in connected_pairs
        review = [p for p in pairs if p['status'] in {'review_aabb_only', 'review_oriented_bounds'}
                  or (p['status'] == 'stud_zone_overlap_review_connections' and not p['connection_present'])]
        return dict(moving=m, fixed=f, **transform_dict(new_world), delta=transform_dict(delta),
                    local_placement=dict(ref=moving.reference, at=local[:3, 3].tolist(), matrix=local[:3, :3].tolist()),
                    contact_status=str(mating[0].status),
                    collision=dict(status='blocked' if blocked else 'review_required' if review else 'no_collision_found',
                                   checked_pairs=len(pairs), blocking_issue_count=len(blocked), review_count=len(review)),
                    distance=float(np.linalg.norm(new_world[:3, 3] - old_world[:3, 3])))

    examined, settled = {}, None
    for order in sorted(range(len(pending)), key=lambda i: (pending[i][0], i)):
        if settled is not None and pending[order][0] > settled + TIE:
            break
        entry = examine(*pending[order][1:])
        examined[order] = entry
        if (settled is None and entry is not None and entry['collision']['status'] == 'no_collision_found'
                and entry['contact_status'] == 'confirmed'):
            settled = pending[order][0]
    ranked = [examined[i] for i in sorted(examined) if examined[i] is not None]
    ranked.sort(key=lambda c: (c['collision']['status'] == 'blocked', c['collision']['review_count'],
                               c['contact_status'] != 'confirmed', c['distance']))
    for index, entry in enumerate(ranked):
        entry['candidate'] = index
    return dict(candidates=ranked[:LIMIT], candidate_count=len(ranked), checked_candidates=len(examined))
