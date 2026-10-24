"""Derive a template (colour roles, stretch cuts, wall slices, part swaps, optional sub-assemblies) from any model.

    from ldraw_tools.templates import derive
    data, report = derive.derive_from_mpd(text, parts, id='cottage', title='Cottage', families=['house'], kinds=['cottage'])

Nothing is hand-written per model. The model is flattened to its physical parts, roles come from the plan (or from
`roles.classify`), and every knob is offered only after the variant it produces was built and checked: no validation
error that the source did not already have, and no more disconnected components than the source.
"""
from __future__ import annotations

import collections
import copy
import re

import numpy as np

from .. import roles
from ..builder import build_plan
from ..editor import import_mpd
from ..geometry import analyze_geometry
from .core import COURSE, STUD, build

DELTA_RANGE = (-8, 12)
COURSE_RANGE = (-1, 6)
MAX_CANDIDATES = 5
OPTIONAL_ROLES = ('detail', 'engine', 'porch', 'chimney')
SLICE_ROLES = {'wall', 'body', 'structure'}
NO_SLICE_ROLES = {'roof', 'base', 'wheel', 'tyre', 'detail', 'engine', 'wing'}
KEEP_ROLES = {'roof', 'wall', 'base'}     # a sub-assembly that is mostly roof or wall is not offered as optional...
OPTIONAL_NAMES = re.compile(r'chimney|porch|dormer|garden|field|fence|tree|plant|bush|lamp|stair|step|trailer|swing|'
                            r'chair|bench|antenna|mirror|spoiler|ladder|crane|flag|sign')   # ...unless its name says it is
KEEP_NAMES = re.compile(r'roof|wall|shell|floor|base|foundation|storey|story|main|body|chassis|cab\b|hull|fuselage')
CLEAN_CODE = re.compile(r'^\d+[a-z]?$')

# Equivalent sets: same footprint and height, different surface. Each pair is verified against the library's boxes.
SWAP_CANDIDATES = {
    'brick_1x2': ['3004', '2877', '98283', '3065'],     # plain, grille, embossed bricks, clear
    'brick_1x4': ['3010', '30137'],                     # plain, log
    'tile_1x2': ['3069b', '2412b'],                     # groove, grille
}


# ------------------------------------------------------------------------------------------------ part families

_FAMILY = re.compile(r'(\d+) x (\d+)')


def family_key(description):
    """'Brick  2 x  4' -> 'Brick N x M' (None for 3-dimension parts and parts without a stud footprint)."""
    text = ' '.join(description.split())
    if text.startswith('Tile '):
        text = text.removesuffix(' with Groove')          # every modern tile has a groove; a grooved brick is a different part
    if re.search(r'\d+ x \d+ x \d+', text) or text[:1] in '~_=|':
        return None
    found = _FAMILY.search(text)
    return text[:found.start()] + 'N x M' + text[found.end():] if found else None


_FAMILY3 = re.compile(r'(\d+) x (\d+) x ')
_ASSEMBLY = re.compile(r'window|door|glass|pane|windscreen|frame|shutter', re.IGNORECASE)


def stretch_key(description):
    """family_key, plus families of 3-dimension parts of one height ('Brick 2 x 4 x 1.333 with Curved Top' ->
    'Brick N x M x 1.333 with Curved Top'); never for windows, doors and glass, which come in matched assemblies."""
    key = family_key(description)
    if key:
        return key
    text = ' '.join(description.split())
    found = _FAMILY3.search(text)
    if not found or text[:1] in '~_=|' or _ASSEMBLY.search(text):
        return None
    return text[:found.start()] + 'N x M x ' + text[found.end():]


class Families:
    """Library parts grouped by description family, with their local boxes (built lazily, once per library)."""

    def __init__(self, parts, facts):
        self.parts, self.facts, self._by_key, self._found = parts, facts, None, {}

    def cached(self, code, j, delta, compute):
        key = (code, j, delta)
        if key not in self._found:
            self._found[key] = compute()
        return self._found[key]

    def members(self, description):
        if self._by_key is None:
            self._by_key = collections.defaultdict(list)
            for code, desc in self.parts.by_code.items():
                key = stretch_key(desc or '')
                if key:
                    self._by_key[key].append(code.casefold())
        return self._by_key.get(stretch_key(description) or '', [])


def _box(facts, code):
    return facts.get(code + '.dat')['bounds']


def _same(a, b, tol=0.6):
    return abs(a - b) <= tol


# A quarter turn about the vertical axis, as a local transform: a 2 x 3 brick turned this way fills the place of a 2 x 2
# brick lengthened along its local z. Boxes below are expressed in the original part's local frame.
QUARTER = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])


def _turned(box):
    """Box of a part placed with QUARTER, in the frame it is placed in (x' = z, z' = -x)."""
    lo, hi = box
    return np.array([[lo[2], lo[1], -hi[0]], [hi[2], hi[1], -lo[0]]])


def candidate_for(fam, facts, leaf_code, axis_j, delta_studs):
    """Library part equal to leaf_code but `delta_studs` longer along local axis j (0 = x, 2 = z).

    Returns (code, box in the leaf's local frame, turned) or None; `turned` means the new part is placed with a
    quarter turn (QUARTER) because the library models it with its long side on the other axis.
    """
    desc = facts.get(leaf_code + '.dat')['desc']
    base = _box(facts, leaf_code)
    other = [i for i in range(3) if i != axis_j]
    options = []
    for code in fam.members(desc):
        if not CLEAN_CODE.match(code) and code != leaf_code:
            continue
        for turned in (False, True):
            box = _turned(_box(facts, code)) if turned else _box(facts, code)
            if not all(_same(box[0][i], base[0][i]) and _same(box[1][i], base[1][i]) for i in other):
                continue
            if _same((box[1][axis_j] - box[0][axis_j]) - (base[1][axis_j] - base[0][axis_j]), delta_studs * STUD):
                options.append((turned, code != leaf_code, len(code), code, box))
                break
    if not options:
        return None
    options.sort(key=lambda o: o[:4])
    return options[0][3], options[0][4], options[0][0]


# ------------------------------------------------------------------------------------------------ flattening

def _group_name(entry_id, section):
    if not re.fullmatch(r'p\d+', entry_id):
        return entry_id
    stem = re.sub(r'\.(ldr|mpd|dat)$', '', section, flags=re.IGNORECASE).split(' - ')[-1]
    slug = re.sub(r'[^a-z0-9]+', '_', stem.casefold()).strip('_')
    return f'{slug}_{entry_id}' if slug else entry_id


def leaves_of(plan, parts, facts):
    """Physical parts of a role-tagged plan as template leaves (stable ids l0001..)."""
    flat = roles.flatten(plan, parts, facts)
    stored = roles.role_of(plan)
    counter = collections.Counter()
    out = []
    for n, leaf in enumerate(flat, 1):
        key = leaf['key']
        base = (key[0], key[1].rsplit('-', 1)[0] if key not in stored and '-' in key[1] else key[1])
        role = stored.get(key) or stored.get(base) or 'structure'
        lo, hi = leaf['bounds']
        # every submodel instance above the part, outermost first: (path key, readable name)
        groups = [('/'.join(i for _, i in leaf['chain'][:k + 1]), _group_name(leaf['chain'][k][1], leaf['chain'][k + 1][0]),
                   leaf['chain'][k + 1][0]) for k in range(len(leaf['chain']) - 1)]
        counter[role] += 1
        out.append(dict(id=f'l{n:04d}', ref=leaf['ref'], colour=int(leaf['colour']), role=role, groups=groups,
                        at=[round(float(v), 6) for v in leaf['origin']], matrix=[[round(float(v), 6) for v in row] for row in leaf['mat']],
                        box=[[round(float(v), 3) for v in lo], [round(float(v), 3) for v in hi]],
                        ub=round(float(-hi[1]), 3), ut=round(float(-lo[1]), 3), kind=leaf['facts']['kind']))
    return out


# -------------------------------------------------------------------------------------------------- stretching

def _local_axis(leaf, world_axis):
    """Local axis (0 = x, 2 = z) that runs along the world axis, or None (tilted parts and lying parts)."""
    m = np.array(leaf['matrix'])
    j = int(np.argmax(np.abs(m[world_axis])))
    return j if abs(abs(m[world_axis, j]) - 1) < 1e-3 and j in (0, 2) else None


FLOAT_ROLES = {'detail', 'trim'}


def _floats(leaf, axis):
    """A small detail or trim part a cut crosses but cannot lengthen: it moves with the side its centre is on.

    Whether that keeps it attached is not assumed here; every offered stretch is built and checked afterwards.
    """
    lo, hi = leaf['box'][0][axis], leaf['box'][1][axis]
    if hi - lo <= STUD + 0.5 and leaf['role'] not in ('wall', 'body', 'chassis', 'base'):
        return True                                      # a 1 x 1 clip, cone or round plate on a roof or a wing
    return (leaf['role'] in FLOAT_ROLES or leaf['kind'] in ('detail', 'light')) and hi - lo <= 2 * STUD + 0.5


def _extensions(leaf, axis, j, fam, facts, deltas):
    """{delta studs: entry} to make a part `delta` studs longer along world `axis`, its min edge fixed in world space
    whatever the part's local origin is. An entry is (code, origin move, turned) for the same part in another length
    (turned = 1: placed with a quarter turn), or, when the library has no such length (a 1 x 3 brick has no 1 x 5),
    (code, 0, 0, filler code, filler origin offset, filler turned): the part stays and a filler of the same family
    and cross-section closes the gap after it (1 x 3 + 1 x 2). Fillers are only used to lengthen."""
    code = leaf['ref'].casefold().removesuffix('.dat')
    sign = 1 if leaf['matrix'][axis][j] > 0 else -1
    base = _box(facts, code)
    length = round((base[1][j] - base[0][j]) / STUD)
    options = {}
    for d in deltas:
        found = fam.cached(code, j, d, lambda c=code, jj=j, dd=d: candidate_for(fam, facts, c, jj, dd))
        if found is not None:
            new_code, box, turned = found
            dpos = (base[0][j] - box[0][j]) if sign > 0 else (box[1][j] - base[1][j])
            options[d] = (new_code, round(float(dpos), 4), int(turned))
            continue
        if d <= 0:
            continue
        filler = fam.cached(code, j, d - length, lambda c=code, jj=j, dd=d - length: candidate_for(fam, facts, c, jj, dd))
        if filler is None:
            continue
        f_code, f_box, f_turned = filler
        # the filler's min edge (world) goes where the part's max edge is
        edge = base[1][j] if sign > 0 else -base[0][j]
        offset = edge - (f_box[0][j] if sign > 0 else -f_box[1][j])
        options[d] = (code, 0.0, 0, f_code, round(float(offset), 4), int(f_turned))
    return options


SEAM_WINDOW = 3 * STUD
SEAM_TARGETS = (0.5, 0.4, 0.6, 0.3, 0.7)


class _Seams:
    """Cuts through a model along one world axis, for stretching.

    The model is split into height bands (between consecutive part bottoms and tops). A cut gives every band a position;
    a plane cut uses the same one everywhere, a seam may step around parts that cannot be lengthened, the way a real
    bonded wall would be cut. A cut is accepted only if, after stretching:

      * every part is wholly left (stays), wholly right (moves) or crosses the cut in every band it occupies and is
        lengthened (a longer sibling, or itself plus a filler of its family);
      * no hole opens: in a band where material ran continuously across the cut, some part there is lengthened. When
        none crosses, a part that ends (or starts) exactly at the cut is lengthened instead;
      * small loose details crossing the cut go with the side their centre is on.
    """

    def __init__(self, leaves, axis, fam, facts, skip=()):
        self.axis, self.fam, self.facts, self.skip = axis, fam, facts, set(skip)
        scenery = _scenery(leaves)               # a garden around a house stays where it is; the house grows
        self.solid = [l for l in leaves if l['kind'] != 'baseplate' and l['id'] not in scenery]
        levels = sorted({round(l['ub'], 1) for l in self.solid} | {round(l['ut'], 1) for l in self.solid})
        self.bands = list(zip(levels, levels[1:]))
        self.members = [[] for _ in self.bands]
        self.span = {}
        for l in self.solid:
            idx = [i for i, (lo, hi) in enumerate(self.bands) if lo >= l['ub'] - 0.05 and hi <= l['ut'] + 0.05]
            self.span[l['id']] = idx
            for i in idx:
                self.members[i].append(l)
        self._options = {}
        self.deltas = [d for d in range(DELTA_RANGE[0], DELTA_RANGE[1] + 1) if d]
        self.support = {}          # a small loose part goes with the part it sits on (or hangs from)
        for l in self.solid:
            if not _floats(l, axis):
                continue
            for other in self.solid:
                if other is l:
                    continue
                touching = abs(other['ut'] - l['ub']) < 1.5 or abs(other['ub'] - l['ut']) < 1.5
                overlap = all(min(l['box'][1][k], other['box'][1][k]) - max(l['box'][0][k], other['box'][0][k]) > 1 for k in (0, 2))
                if touching and overlap and not _floats(other, axis):
                    self.support[l['id']] = other
                    break

    def extendable(self, l):
        if l['id'] not in self._options:
            j = _local_axis(l, self.axis)
            ok = j is not None and l['id'] not in self.skip and stretch_key(self.facts.get(l['ref'])['desc'])
            self._options[l['id']] = _extensions(l, self.axis, j, self.fam, self.facts, self.deltas) if ok else {}
        return self._options[l['id']]

    def ends(self, l):
        return l['box'][0][self.axis], l['box'][1][self.axis]

    def side(self, l, c):
        lo, hi = self.ends(l)
        if hi <= c + 0.5:
            return 'L'
        if lo >= c - 0.5:
            return 'R'
        if _floats(l, self.axis) and not self.extendable(l):
            return 'L' if (lo + hi) / 2 < c else 'R'
        return 'E'

    def across(self, l):
        """The part's extent on the other horizontal axis (a car's two side walls are in the same band)."""
        other = 2 if self.axis == 0 else 0
        return l['box'][0][other], l['box'][1][other]

    def holes(self, i, c, lengthened=()):
        """Places in band i where material ran continuously across cut c and nothing there is lengthened: pairs of
        (part ending at c, part starting at c) that face each other, with the extent they share across."""
        out = []
        ends_here = [l for l in self.members[i] if abs(self.ends(l)[1] - c) <= 0.5]
        starts_here = [l for l in self.members[i] if abs(self.ends(l)[0] - c) <= 0.5]
        covered = [self.across(l) for l in self.members[i]
                   if l['id'] in lengthened or (self.ends(l)[0] < c - 0.5 < c + 0.5 < self.ends(l)[1] and self.extendable(l))]
        for a in ends_here:
            for b in starts_here:
                lo = max(self.across(a)[0], self.across(b)[0])
                hi = min(self.across(a)[1], self.across(b)[1])
                if hi - lo > 1 and not any(min(hi, y) - max(lo, x) > 1 for x, y in covered):
                    out.append((a, b, lo, hi))
        return out

    def band_ok(self, i, c):
        """(valid, has holes to close) for cut position c in band i."""
        for l in self.members[i]:
            lo, hi = self.ends(l)
            if lo < c - 0.5 and hi > c + 0.5 and not self.extendable(l) and not _floats(l, self.axis):
                return False, False
        holes = self.holes(i, c)
        return all(self.extendable(a) or self.extendable(b) for a, b, _, _ in holes), bool(holes)

    def solve(self, target, window):
        """(extend {id: options}, shift ids, allowed deltas) of the best cut near `target`, or None."""
        choices = []
        for i, band in enumerate(self.members):
            if not band:
                choices.append([None])
                continue
            edges = {target}
            if window:
                edges |= {round(v * 2) / 2 for l in band for v in self.ends(l) if abs(v - target) <= window}
            valid = [c for c in sorted(edges) if self.band_ok(i, c)[0]]
            if not valid:
                return None
            choices.append(valid)
        best = {c: (abs(c - target) if c is not None else 0.0, [c]) for c in choices[0]}
        for i in range(1, len(self.bands)):
            shared = [l for l in self.members[i] if i - 1 in self.span[l['id']]]
            nxt = {}
            for c in choices[i]:
                for p, (cost, path) in best.items():
                    here = c if c is not None else p
                    if here is not None and p is not None and c is not None and \
                            any(self.side(l, p) != self.side(l, c) for l in shared):
                        continue
                    # straight is best: every step of the seam costs as much as being two studs off the target
                    total = cost + (abs(here - target) if here is not None else 0.0) + \
                        (2 * STUD if here is not None and p is not None and abs(here - p) > 0.5 else 0.0)
                    if here not in nxt or total < nxt[here][0]:
                        nxt[here] = (total, path + [c])
            best = nxt
            if not best:
                return None
        path = min(best.values(), key=lambda v: v[0])[1]
        cut_of = {}
        for l in self.solid:
            cuts = [path[i] for i in self.span[l['id']] if path[i] is not None]
            cut_of[l['id']] = cuts[0] if cuts else target
        lengthen = {l['id'] for l in self.solid if self.side(l, cut_of[l['id']]) == 'E'}
        # close the holes: where material ran across the cut and nothing there is lengthened, lengthen a part that
        # ends (preferred) or starts exactly at the cut, if it does so at the cut of every band it reaches through
        for i, c in enumerate(path):
            if c is None:
                continue
            for a, b, _, _ in self.holes(i, c, lengthen):
                if self.holes(i, c, lengthen) and not any(h[0] is a and h[1] is b for h in self.holes(i, c, lengthen)):
                    continue                                 # closed by a part lengthened for an earlier hole
                for l in (a, b):
                    lo, hi = self.ends(l)
                    if self.extendable(l) and all(path[k] is not None and (abs(hi - path[k]) <= 0.5 or abs(lo - path[k]) <= 0.5)
                                                  for k in self.span[l['id']]):
                        lengthen.add(l['id'])
                        break
                else:
                    return None
        extend, shift, allowed, left = {}, [], None, 0
        for l in self.solid:
            where = self.side(l, cut_of[l['id']])
            lo, hi = self.ends(l)
            if l['id'] not in lengthen and lo < cut_of[l['id']] - 0.5 < cut_of[l['id']] + 0.5 < hi and l['id'] in self.support:
                base = self.support[l['id']]          # a detail crossing the cut follows what it sits on
                where = 'L' if base['id'] in lengthen else self.side(base, cut_of[base['id']])
                where = 'L' if where == 'E' else where
            if l['id'] in lengthen:
                found = self.extendable(l)
                allowed = set(found) if allowed is None else allowed & set(found)
                extend[l['id']] = found
            elif where == 'R':
                shift.append(l['id'])
            else:
                left += 1
        if not allowed or len(extend) < 2 or min(left, len(shift)) < max(2, 0.15 * len(self.solid)):
            return None          # a cut at the model's edge would only lengthen a few loose parts
        return {k: {str(d): list(v[d]) for d in sorted(allowed)} for k, v in extend.items()}, shift, sorted(allowed)


def stretch_candidates(leaves, axis, fam, facts, skip=()):
    """Plane cuts along a world axis, ranked by how many sizes they offer, then by how central they are."""
    seams = _Seams(leaves, axis, fam, facts, skip)
    if len(seams.solid) < 12:
        return []
    bounds = sorted({round(l['box'][i][axis] * 2) / 2 for l in seams.solid for i in (0, 1)})
    mid = (bounds[0] + bounds[-1]) / 2
    rows = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a <= 2.5:
            continue
        cut = (a + b) / 2
        found = seams.solve(cut, 0)
        if found:
            rows.append((-(min(len(found[2]), 6)), abs(cut - mid), cut, found))
    rows.sort(key=lambda r: r[:3])
    return [(cut, *found) for _, _, cut, found in rows[:MAX_CANDIDATES]]


def seam_candidates(leaves, axis, fam, facts, skip=()):
    """Seams (cuts that step around parts which cannot be lengthened) near a few targets along the axis."""
    seams = _Seams(leaves, axis, fam, facts, skip)
    if len(seams.solid) < 12:
        return []
    low = min(l['box'][0][axis] for l in seams.solid)
    high = max(l['box'][1][axis] for l in seams.solid)
    out, seen = [], set()
    for share in SEAM_TARGETS:
        target = round(low + share * (high - low), 2)
        found = seams.solve(target, SEAM_WINDOW)
        if found:
            key = (frozenset(found[0]), frozenset(found[1]))
            if key not in seen:
                seen.add(key)
                out.append((target, *found))
    return out


def _spec(cuts):
    """Stretch spec of one axis from accepted cuts: the cuts, and for every reachable total the delta at each cut."""
    best = {0: ()}
    for cut in cuts:
        nxt = {}
        for total, combo in best.items():
            for d in (0, *cut['delta']):
                t = total + d
                if -12 <= t <= 24 and (t not in nxt or sum(x * x for x in combo + (d,)) < sum(x * x for x in nxt[t])):
                    nxt[t] = combo + (d,)
        best = nxt
    return dict(cuts=[dict(cut=c['cut'], extend=c['extend'], shift=c['shift']) for c in cuts],
                plans={str(t): list(combo) for t, combo in sorted(best.items())})


# ------------------------------------------------------------------------------------------- repeating slices

REPEAT_COUNTS = (-1, 1, 2, 3)          # remove the slice, or add one to three more copies of it
MAX_SLICE = 8 * STUD
BAY_ROLES = {'wall', 'window', 'door', 'body', 'glass', 'roof', 'chassis', 'structure', 'trim', 'wing'}


def repeat_candidates(leaves, axis, fam, facts):
    """Slices [c1, c2] along a world axis that can be repeated or removed: every part lies wholly left of c1, wholly
    right of c2, wholly inside (copied), or spans the whole slice (lengthened by the slice width per copy). A wall bay
    with its window, a train car's panel section, a run of fence. Ranked by how many counts they allow, then by how
    central they are. Returns [dict(c1, c2, width, inside, right, spans, counts)]."""
    solid = [l for l in leaves if l['kind'] != 'baseplate']
    if len(solid) < 12:
        return []
    eps = 0.5
    edges = sorted({round(l['box'][i][axis] * 2) / 2 for l in solid for i in (0, 1)})
    mid = (edges[0] + edges[-1]) / 2
    spans_of = [(l, l['box'][0][axis], l['box'][1][axis]) for l in solid]
    rows = []
    for c1 in edges:
        for c2 in edges:
            width = c2 - c1
            if width < STUD - eps or width > MAX_SLICE + eps or abs(width / STUD - round(width / STUD)) > 0.03:
                continue
            studs = round(width / STUD)
            left, inside, right, spans, ok = 0, [], [], {}, True
            for leaf, lo, hi in spans_of:
                if hi <= c1 + eps:
                    left += 1
                elif lo >= c2 - eps:
                    right.append(leaf['id'])
                elif lo >= c1 - eps and hi <= c2 + eps:
                    inside.append(leaf)
                elif lo < c1 - eps and hi > c2 + eps:
                    spans[leaf['id']] = leaf
                elif _floats(leaf, axis):              # a small detail crossing one cut goes with its centre
                    centre = (lo + hi) / 2
                    if centre < c1:
                        left += 1
                    elif centre > c2:
                        right.append(leaf['id'])
                    else:
                        inside.append(leaf)
                else:
                    ok = False
                    break
            total = len(solid)
            if not ok or len(inside) < 3 or min(left, len(right)) < max(2, 0.15 * total):
                continue
            if not any(l['role'] in BAY_ROLES and not _floats(l, axis) for l in inside):
                continue
            counts = set(REPEAT_COUNTS)
            options = {}
            for lid, leaf in spans.items():
                j = _local_axis(leaf, axis)
                if j is None or not stretch_key(facts.get(leaf['ref'])['desc']):
                    counts = set()
                    break
                found = _extensions(leaf, axis, j, fam, facts, [k * studs for k in sorted(counts)])
                counts &= {k for k in counts if k * studs in found}
                options[lid] = found
                if not counts:
                    break
            if not any(k > 0 for k in counts):
                continue
            spans_out = {lid: {str(k): list(options[lid][k * studs]) for k in sorted(counts)} for lid in spans}
            rows.append((-len(counts), abs((c1 + c2) / 2 - mid), -len(inside), c1,
                         dict(c1=c1, c2=c2, width=studs * STUD, inside=[l['id'] for l in inside], right=right,
                              spans=spans_out, counts=sorted(counts))))
    rows.sort(key=lambda r: r[:4])
    return [r[-1] for r in rows[:MAX_CANDIDATES]]


FOOTPRINT_ROLES = {'wall', 'window', 'door', 'roof', 'body', 'glass', 'structure'}


def _footprint(leaves):
    """World x/z rectangle of the model's main mass (under the roof, else walls and body), so a garden or scenery
    around it is left alone."""
    core = ([l for l in leaves if l['role'] == 'roof'] or
            [l for l in leaves if l['role'] in FOOTPRINT_ROLES and l['kind'] != 'baseplate'] or leaves)
    return (min(l['box'][0][0] for l in core), max(l['box'][1][0] for l in core),
            min(l['box'][0][2] for l in core), max(l['box'][1][2] for l in core))


def _scenery(leaves):
    """Ids of the parts around a building (garden, paths, lamps): outside the rectangle under its roof and not touching
    the house through other parts. Empty for anything that is not a building with walls and a roof."""
    if not any(l['role'] == 'wall' for l in leaves) or not any(l['role'] == 'roof' for l in leaves):
        return set()
    rect = _footprint(leaves)
    solid = [l for l in leaves if l['kind'] != 'baseplate']
    attached = [l for l in solid if _inside(l, rect)]
    near = (rect[0] - 2 * STUD, rect[1] + 2 * STUD, rect[2] - 2 * STUD, rect[3] + 2 * STUD)
    outside = [l for l in solid if not _inside(l, rect) and _inside(l, near, margin=-0.5)]   # within two studs of it

    def touch(a, b):
        return all(min(a['box'][1][k], b['box'][1][k]) - max(a['box'][0][k], b['box'][0][k]) > -0.5 for k in range(3))
    # what touches the house within two studs of its walls (a flower box, a fence against its corner) moves with it;
    # everything else around it (a tree, a swing, a lamp post on the baseplate) is scenery and stays
    reached, frontier = set(), list(attached)
    while frontier:
        nxt = []
        for l in outside:
            if l['id'] not in reached and any(touch(l, f) for f in frontier):
                reached.add(l['id'])
                nxt.append(l)
        frontier = nxt
    # whatever those parts stand on or carry goes with them (the pillar under an arch that springs from the wall)
    others = [l for l in solid if not _inside(l, rect)]
    stacked = attached + [l for l in others if l['id'] in reached]
    while stacked:
        nxt = []
        for l in others:
            if l['id'] in reached:
                continue
            for f in stacked:
                above_below = abs(l['ut'] - f['ub']) < 1.5 or abs(l['ub'] - f['ut']) < 1.5
                if above_below and all(min(l['box'][1][k], f['box'][1][k]) - max(l['box'][0][k], f['box'][0][k]) > 1 for k in (0, 2)):
                    reached.add(l['id'])
                    nxt.append(l)
                    break
        stacked = nxt
    return {l['id'] for l in solid if not _inside(l, rect) and l['id'] not in reached}


def _inside(leaf, rect, margin=1.0):
    x0, x1, z0, z1 = rect
    lo, hi = leaf['box']
    return min(hi[0], x1) - max(lo[0], x0) > margin and min(hi[2], z1) - max(lo[2], z0) > margin


def course_candidates(leaves):
    """Cut heights (up coordinates) where one 24-LDU slice of wall can be repeated: nothing crosses either plane.

    Only the model's main mass counts (its footprint rectangle): a lamp post or a tree in the garden that crosses
    the plane stays where it is, and only what stands inside the footprint moves up. Returns
    [(slice size, cut, slice ids, ids that move)].
    """
    tol = 6.0
    rect = _footprint(leaves)
    mass = [l for l in leaves if _inside(l, rect) and l['kind'] != 'baseplate']
    ups = sorted({round(l['ub']) for l in mass})
    out = []
    for cut in ups:
        if any(l['ub'] < cut - tol and l['ut'] > cut + tol for l in mass):
            continue
        if any(l['ub'] < cut - COURSE - tol and l['ut'] > cut - COURSE + tol for l in mass):
            continue
        slice_ = [l for l in mass if l['ub'] >= cut - COURSE - tol and l['ut'] <= cut + tol and l['ub'] <= cut - COURSE + tol]
        above = [l for l in mass if l['ub'] >= cut - tol and l not in slice_]
        main = sum(l['role'] in SLICE_ROLES for l in slice_)
        if len(slice_) < 4 or not above or main < 0.6 * len(slice_) or any(l['role'] in NO_SLICE_ROLES for l in slice_):
            continue
        out.append((len(slice_), cut, [l['id'] for l in slice_], [l['id'] for l in above]))
    if not out:
        return []
    mid = np.median([l['ub'] for l in mass])
    out.sort(key=lambda r: (abs(r[1] - mid), -r[0]))
    return out[:MAX_CANDIDATES]


# -------------------------------------------------------------------------------------- groups, swaps, colours

def optional_groups(leaves):
    """Sub-assemblies nothing rests on: submodel instances and optional roles, each with the leaf ids that go with it."""
    def rests_on(group_ids):
        ids = set(group_ids)
        tops = [l for l in leaves if l['id'] in ids]
        for other in leaves:
            if other['id'] in ids:
                continue
            for g in tops:
                overlap = (min(g['box'][1][0], other['box'][1][0]) - max(g['box'][0][0], other['box'][0][0]) > 1 and
                           min(g['box'][1][2], other['box'][1][2]) - max(g['box'][0][2], other['box'][0][2]) > 1)
                if overlap and abs(other['ub'] - g['ut']) < 6 and other['ub'] <= g['ut'] + 6 and other['ut'] > g['ut']:
                    return True
        return False
    total = len(leaves)
    found = {}
    by_group, names, section_of = collections.defaultdict(list), {}, {}
    for l in leaves:
        for key, name, section in l.get('groups', ()):   # submodel instances at every depth: a garden lamp in the house
            by_group[key].append(l['id'])
            names[key], section_of[key] = name, section
    instances = collections.defaultdict(list)          # section -> optional instances of it
    role_of = {l['id']: l['role'] for l in leaves}
    seen = set()
    for key, ids in sorted(by_group.items(), key=lambda kv: (kv[0].count('/'), kv[0])):
        name = names[key]
        main = collections.Counter(role_of[i] for i in ids).most_common(1)[0][0]
        named = name.casefold()
        keep = bool(KEEP_NAMES.search(named)) or (main in KEEP_ROLES and not OPTIONAL_NAMES.search(named))
        if 3 <= len(ids) <= 0.4 * total and not keep and frozenset(ids) not in seen:
            seen.add(frozenset(ids))
            slug = re.sub(r'[^a-z0-9]+', '_', f'part_{name}'.casefold()).strip('_')
            while slug in found:
                slug += '_b'
            found[slug] = dict(label=f'sub-assembly {name}', leaves=ids)
            instances[section_of[key]].append(slug)
    for section, slugs in instances.items():          # four identical filler panels are one knob, not four
        if len(slugs) < 2:
            continue
        stem = re.sub(r'\.(ldr|mpd|dat)$', '', section, flags=re.IGNORECASE).split(' - ')[-1]
        ids = [i for slug in slugs for i in found.pop(slug)['leaves']]
        if len(ids) <= 0.4 * total:
            found['part_' + re.sub(r'[^a-z0-9]+', '_', stem.casefold()).strip('_') + '_all'] = dict(
                label=f'all {len(slugs)} {stem}', leaves=ids)
    for role in OPTIONAL_ROLES:
        ids = [l['id'] for l in leaves if l['role'] == role]
        if 2 <= len(ids) <= 0.4 * total:
            found[role] = dict(label=f'all {role} parts', leaves=ids)
    return {k: v for k, v in found.items() if not rests_on(v['leaves'])}


def swap_sets(leaves, facts):
    """Equivalent part sets present in the model whose boxes agree in the library."""
    present = {l['ref'].casefold().removesuffix('.dat'): l for l in leaves}
    out = {}
    for name, codes in SWAP_CANDIDATES.items():
        used = [c for c in codes if c in present]
        if not used:
            continue
        base = _box(facts, used[0])
        same = [c for c in codes if all(_same(x, y) for x, y in zip(_box(facts, c).ravel(), base.ravel(), strict=True))]
        if len(same) > 1:
            out[name] = dict(codes=same, labels={c: facts.get(c + '.dat')['desc'] for c in same},
                             leaves=[l['id'] for l in leaves if l['ref'].casefold().removesuffix('.dat') in same])
    return out


# ------------------------------------------------------------------------------------------------- validation

def check_plan(plan, parts):
    """(error messages, optimistic component count) of a plan, built and measured the same way as the generators' tests."""
    _, model, diagnostics = build_plan(plan, parts)
    errors = [d['message'] for d in diagnostics if d['severity'] == 'error']
    geometry = analyze_geometry(model, parts, detail='summary', contacts='all')
    errors += [d['message'] for d in geometry['diagnostics'] if d['severity'] == 'error']
    return errors, geometry['optimistic_component_count']


def _progressive(check, deltas, verdicts):
    """The deltas that pass `check`, walking out from zero both ways and stopping at the first failure each way:
    the offered range is contiguous and every value in it was built and checked."""
    passed = []
    for side in (sorted(d for d in deltas if d > 0), sorted((d for d in deltas if d < 0), reverse=True)):
        for d in side:
            ok, info = check(d)
            verdicts[d] = info
            if not ok:
                break
            passed.append(d)
    return sorted(passed)


def _ok(data, params, parts, baseline):
    errors, components = check_plan(build(data, params)[0], parts)
    return len(errors) <= baseline[0] and components <= baseline[1], dict(errors=len(errors), components=components)


# ----------------------------------------------------------------------------------------------------- derive

def derive(plan, parts, *, id, title, families, kinds, description, source=None, hints='', verify=True, facts=None,
           log=None):
    """(template data, report) from a plan. `plan` may already carry roles; missing ones are classified."""
    log = log or (lambda *_: None)
    facts = facts or roles.PartFacts(parts)
    tagged, tag_report = roles.tag_plan(plan, parts, hints=hints or description, facts=facts)
    leaves = leaves_of(tagged, parts, facts)
    fam = Families(parts, facts)
    report = dict(leaves=len(leaves), domain=tag_report['domain'], verified={}, rejected={})
    data = dict(version=1, id=id, title=title, families=list(families), kinds=list(kinds), description=description,
                source=source or {}, leaves=leaves, stretch={}, repeat={}, courses=None, swaps={}, groups={})
    colour_counts = collections.defaultdict(collections.Counter)
    for l in leaves:
        colour_counts[l['role']][l['colour']] += 1
    data['colours'] = {r: c.most_common(1)[0][0] for r, c in sorted(colour_counts.items())}
    data['roles'] = sorted(colour_counts)
    baseline = check_plan(build(copy.deepcopy(data), {})[0], parts) if verify else (10 ** 6, 10 ** 6)
    baseline = (len(baseline[0]), baseline[1]) if verify else baseline
    report['baseline'] = dict(errors=baseline[0], components=baseline[1])
    log('baseline', report['baseline'])

    param_name = {'x': 'x_delta', 'z': 'z_delta'}
    axes = {'x': 0, 'z': 2}
    straddled = set()      # parts already extended along another axis: a second axis leaves them alone
    for axis, index in axes.items():
        accepted, tried = [], []
        found = stretch_candidates(leaves, index, fam, facts, straddled) + seam_candidates(leaves, index, fam, facts, straddled)
        for cut, extend, shift, allowed in found:
            if set(extend) & {i for c in accepted for i in c['extend']}:
                continue                                     # cuts must not share a part: each part changes at one cut only
            cand = dict(cut=cut, extend=extend, shift=shift, delta=allowed)
            verdicts, ok_all = {}, True
            if verify:
                passed = _progressive(lambda d, a=axis, c=cand: _ok(dict(data, stretch={a: _spec([c])}), {param_name[a]: d},
                                                                    parts, baseline), allowed, verdicts)
                ok_all = bool(passed)
                cand['delta'] = passed
                cand['extend'] = {k: {d: v for d, v in o.items() if int(d) in passed} for k, o in extend.items()}
            tried.append(dict(cut=cut, deltas=allowed, ok=ok_all, checks={str(k): v for k, v in verdicts.items()}))
            if ok_all:
                accepted.append(cand)
            if len(accepted) == 2:
                break
        while accepted:
            spec = _spec(accepted)
            totals = [int(t) for t in spec['plans'] if int(t)]
            ok_all, verdicts = True, {}
            if verify and len(accepted) > 1:
                for d in sorted(totals, key=abs):                          # every size the combination offers
                    ok, info = _ok(dict(data, stretch={axis: spec}), {param_name[axis]: d}, parts, baseline)
                    verdicts[d] = info
                    ok_all = ok_all and ok
                    if not ok:
                        break
            if ok_all:
                data['stretch'][axis] = spec
                straddled |= {i for c in accepted for i in c['extend']} if axis == 'x' else set()
                report['verified'][axis] = dict(cuts=[c['cut'] for c in accepted], totals=sorted(totals),
                                                combined_checks={str(k): v for k, v in verdicts.items()})
                break
            accepted.pop()                                   # the combination failed: fall back to fewer cuts
        report['rejected'][axis] = [t for t in tried if not t['ok']]
        log('stretch', axis, report['verified'].get(axis))
        if axis in data['stretch']:
            continue                      # one length knob per axis: a slice and a cut on the same axis would interfere
        for cand in repeat_candidates(leaves, index, fam, facts):
            verdicts = {}
            passed = cand['counts']
            if verify:
                passed = _progressive(lambda k, a=axis, c=cand: _ok(dict(data, repeat={a: c}), {f'{a}_repeat': k}, parts,
                                                                    baseline), cand['counts'], verdicts)
            if any(k > 0 for k in passed):
                cand = dict(cand, counts=passed, spans={i: {k: v for k, v in o.items() if int(k) in passed}
                                                          for i, o in cand['spans'].items()})
                data.setdefault('repeat', {})[axis] = cand
                report['verified'][f'{axis}_repeat'] = dict(c1=cand['c1'], c2=cand['c2'], width=cand['width'],
                                                            inside=len(cand['inside']), counts=passed,
                                                            checks={str(k): v for k, v in verdicts.items()})
                break
            report['rejected'].setdefault(f'{axis}_repeat', []).append(
                dict(c1=cand['c1'], c2=cand['c2'], checks={str(k): v for k, v in verdicts.items()}))
        log('repeat', axis, report['verified'].get(f'{axis}_repeat'))
    for _count, cut, ids, moving in course_candidates(leaves):
        spec = dict(cut=cut, height=COURSE, slice=ids, above=moving, delta=list(range(COURSE_RANGE[0], COURSE_RANGE[1] + 1)))
        verdicts = {}
        if verify:
            passed = _progressive(lambda d, c=spec: _ok(dict(data, courses=c), {'courses_delta': d}, parts, baseline),
                                  spec['delta'], verdicts)
            spec['delta'] = passed
        ok_all = any(d > 0 for d in spec['delta'])
        if ok_all:
            data['courses'] = spec
            report['verified']['courses'] = dict(cut=cut, slice=len(ids), checks={str(k): v for k, v in verdicts.items()})
            break
        report['rejected'].setdefault('courses', []).append(dict(cut=cut, checks={str(k): v for k, v in verdicts.items()}))
    log('courses', report['verified'].get('courses'))
    for name, group in optional_groups(leaves).items():
        trial = dict(data, groups={name: group})
        ok, info = _ok(trial, {f'include_{name}': False}, parts, baseline) if verify else (True, {})
        if ok:
            data['groups'][name] = group
            report['verified'][f'include_{name}'] = info
        else:
            report['rejected'][f'include_{name}'] = info
        if len(data['groups']) >= 6:
            break
    for name, swap in swap_sets(leaves, facts).items():
        data['swaps'][name] = swap
        report['verified'][f'swap_{name}'] = dict(codes=swap['codes'])
    data['params'] = _params(data, leaves)
    if verify:      # every knob above was checked on its own; record how the extreme combinations fare too
        report['corners'] = {}
        for corner in ('min', 'max'):
            pick = {k: (min if corner == 'min' else max)(v['enum']) for k, v in data['params'].items()
                    if k.endswith(('_delta', '_repeat'))}
            if pick:
                ok, info = _ok(data, pick, parts, baseline)
                report['corners'][corner] = dict(params=pick, ok=ok, **info)
    report['size'] = {a: s['plans'] and max(int(t) for t in s['plans']) for a, s in data['stretch'].items()}
    data['coverage'] = _coverage(data)
    data['quality'] = (f"Derived from {source.get('name', 'a model') if source else 'a model'}: {len(leaves)} parts, roles by classifier "
                       f"({tag_report['domain']}); every offered size, swap and removal was built and checked.")
    return data, report


def compact(data):
    """The template JSON as stored: derivation-only fields (world boxes, kinds) dropped; `generate` never reads them."""
    out = dict(data)
    out['leaves'] = [{k: v for k, v in l.items() if k not in ('box', 'ut', 'kind', 'groups')} for l in data['leaves']]
    return out


def _params(data, leaves):
    params = {}
    for role in data['roles']:
        params[f'colour_{role}'] = {'type': ['string', 'integer'], 'x-colour': True, 'default': data['colours'][role],
                                    'description': f'Colour of the {role} parts (colour name or LDraw code)'}
    for axis, spec in data.get('repeat', {}).items():
        names = collections.Counter(l['role'] for l in leaves if l['id'] in set(spec['inside']))
        params[f'{axis}_repeat'] = {'enum': sorted({0, *spec['counts']}), 'default': 0, 'x-axis': axis,
                                    'x-slice-studs': round(spec['width'] / STUD),
                                    'description': f"Copies of a {round(spec['width'] / STUD)}-stud section along "
                                                   f"{axis.upper()} added (+) or removed (-); it holds "
                                                   + ', '.join(f'{n} {r}' for r, n in names.most_common(3))}
    for axis, spec in data['stretch'].items():
        index = 0 if axis == 'x' else 2
        size = max(l['box'][1][index] for l in leaves if l['kind'] != 'baseplate') - min(l['box'][0][index] for l in leaves if l['kind'] != 'baseplate')
        params[f'{axis}_delta'] = {'enum': sorted(int(t) for t in spec['plans']), 'default': 0, 'x-axis': axis,
                                   'x-size-studs': round(size / STUD, 1),
                                   'description': f'Studs added (+) or removed (-) along {axis.upper()}; the model is {round(size / STUD)} studs long there'}
    if data['courses']:
        params['courses_delta'] = {'enum': sorted({0, *data['courses']['delta']}), 'default': 0,
                                   'description': 'Wall courses repeated (+) or dropped (-)'}
    for name, swap in data['swaps'].items():
        params[f'swap_{name}'] = {'enum': ['keep', *swap['codes']], 'default': 'keep', 'x-labels': swap['labels'],
                                  'description': f'Swap every {name.replace("_", " ")} for an equivalent part'}
    for name, group in data['groups'].items():
        params[f'include_{name}'] = {'type': 'boolean', 'default': True, 'description': f"Keep {group['label']}"}
    return params


def _coverage(data):
    out = ['colours by role']
    out += [f'length along {a.upper()}' for a in data['stretch']]
    out += [f'repeat a section along {a.upper()}' for a in data.get('repeat', {})]
    if data['courses']:
        out.append('wall courses')
    out += [f'swap {n}' for n in data['swaps']]
    out += [f'optional {n}' for n in data['groups']]
    return out


def derive_from_mpd(text, parts, *, name='model.ldr', **kwargs):
    """Import MPD/LDR text as a plan, then derive. Author and licence come from the file header."""
    plan, warnings = import_mpd(text, parts, name=name)
    header = {}
    for line in text.splitlines():
        for key, label in (('Author:', 'author'), ('!LICENSE', 'license')):
            if line.startswith('0 ' + key) and label not in header:
                header[label] = line[2 + len(key):].strip()
    source = {**dict(name=name), **header, **(kwargs.pop('source', None) or {})}
    data, report = derive(plan, parts, source=source, **kwargs)
    report['import_warnings'] = len(warnings)
    return data, report
