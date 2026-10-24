"""Role of every brick in ANY model: roof, wall, window, door, wheel, wing... with a confidence.

Generators stamp exact roles at construction (see generators/). Everything else (an AI build, an import, a
hand-edited model, a reference set) gets them from this module: a deterministic scorer over

  * the part itself: its library description ("Slope Brick 45 2 x 2", "Window 1 x 4 x 3", "Wheel Rim ...");
  * its colour (transparent -> glass or window, never a colour-to-role table);
  * its geometry in the model: height band, share of the footprint, slopes near the top, wheels below the body;
  * the submodel it sits in (its name: "roof", "front-axle", "wing"), and the model's own text for the domain.

    from ldraw_tools import roles
    found = roles.classify(plan, parts, hints='small red house with a chimney')   # {(section, id): Role}
    tagged, report = roles.tag_plan(plan, parts)                                  # plan copy with `role` on placements

Roles are stored per placement in the optional plan field `role`; a role that is already there (from a generator or
a person) is kept unless `overwrite=True`. Nothing here changes geometry or colours.
"""
from __future__ import annotations

import collections
import copy
import re
from dataclasses import dataclass

import numpy as np

from .builder import build_plan

ROLES = ('roof', 'wall', 'window', 'door', 'base', 'wheel', 'tyre', 'chassis', 'body', 'glass', 'wing', 'engine',
         'trim', 'detail', 'structure', 'tower')      # 'tower': stamped by the building grammar, never guessed here

# Roles that the generators stamp, folded into the classifier vocabulary (used to score the classifier on them).
# 'tower' is the building grammar's (ldraw_tools/grammar/roles.py on the build-grammar branch); its other roles
# (roof, wall, window, door, glass, base, detail, trim, structure) are the same words with the same meaning.
CANONICAL = {'porch': 'trim', 'chimney': 'roof', 'accent': 'trim', 'light': 'detail', 'hull': 'body',
             'canopy': 'glass', 'wood': 'detail', 'metal': 'trim', 'headlamp': 'detail', 'tail_lamp': 'detail',
             'tower': 'wall'}

ROLE_WORDS = {   # what a person says -> role (used by role_edits to resolve "the roof", "the windows", "the wheels")
    # A feature word such as "chimney" or "porch" is deliberately absent: it only means something on a plan that has
    # that role (a generator's or the grammar's), and "remove the chimney" must never fall back to "remove the roof".
    'roof': 'roof', 'roofs': 'roof', 'roofing': 'roof', 'wall': 'wall', 'walls': 'wall', 'brickwork': 'wall',
    'masonry': 'wall', 'tower': 'tower', 'towers': 'tower', 'turret': 'tower', 'turrets': 'tower', 'panes': 'glass',
    'glazing': 'glass', 'baseplate': 'base', 'ground': 'base', 'cornice': 'trim', 'plinth': 'trim', 'window': 'window',
    'windows': 'window', 'glass': 'glass', 'windscreen': 'glass', 'windshield': 'glass', 'canopy': 'glass',
    'door': 'door', 'doors': 'door', 'floor': 'base', 'base': 'base', 'foundation': 'base', 'wheel': 'wheel',
    'wheels': 'wheel', 'rim': 'wheel', 'rims': 'wheel', 'tyre': 'tyre', 'tyres': 'tyre', 'tire': 'tyre',
    'tires': 'tyre', 'chassis': 'chassis', 'frame': 'chassis', 'body': 'body', 'bodywork': 'body', 'wing': 'wing',
    'wings': 'wing', 'engine': 'engine', 'engines': 'engine', 'trim': 'trim', 'details': 'detail', 'detail': 'detail',
}


@dataclass(frozen=True)
class Role:
    role: str
    confidence: float
    reason: str


# --------------------------------------------------------------------------------------------- part knowledge

KINDS = [   # first match wins; matched against the lower-cased library description
    ('baseplate', r'^(baseplate|road plate|plate .*baseplate)|^baseplate'),
    ('detail', r'^(minifig|figure|animal|plant|flower|tree|flag|sticker|cloth|belville|scala|hose|string|rubber|chain|label|'
               r'sheet|fabric|duplo|bionicle|hero factory|windscreen wiper|tool|cup|cutlery|food|bottle|helmet|hair|torso|'
               r'head|hips|legs?|arm|hand|neckwear|headwear|hipwear|footwear|stud|display|tag|ring|electric|train track|'
               r'antenna|crane hook|barrel|bucket|chest|treasure|sword|spear|shield|weapon|gun|ladder|balloon|belt|backpack)'),
    ('tyre', r'^(tyre|tire)'),
    ('wheel', r'^wheel(?!.*\barch\b)|wheel rim|\brim\b|^train wheel'),
    ('glass_window', r'^glass for|^pane|^window pane|^door glass'),
    ('windscreen', r'windscreen|windshield|canopy|cockpit|^glass'),
    ('window', r'window|shutter'),
    ('door', r'(^|\s)door(?!\s*(handle|knob))|^garage|\bgate\b'),
    ('wing', r'\bwing\b|aileron|rudder|stabili[sz]er|tailplane|\bflap\b'),
    ('engine', r'engine|exhaust|propeller|rotor|thruster|turbine|\bmotor\b|\bjet\b|nozzle'),
    ('wedgeplate', r'^(wedge plate|plate special|plate, round corner)|^wedge\b.*plate'),
    ('slope', r'^(slope|roof|wedge|cone)|\bslope\b|curved top|brick, curved'),
    ('arch', r'^arch'),
    ('fence', r'fence|railing|^bar\b|stair|chimney|pillar|column|^support|^hinge|^clip|^handle|^lever'),
    ('technic', r'^technic|\baxle\b|^pin\b|\bgear\b|liftarm|^beam|^connector|^turntable|^winch|^rack|^cylinder'),
    ('tile', r'^tile'),
    ('plate', r'^plate'),
    ('brick', r'^(brick|panel|container|wall|baseplate|bracket|jumper|round)'),
    ('light', r'\blight\b|\blamp\b|headlight|torch|beacon'),
    ('vehicle', r'^(vehicle|car\b|train|plane|boat|mudguard|bumper|steering|windscreen|bonnet|hood|fender)|mudguard|bumper|fender'),
]
_KINDS = [(k, re.compile(p)) for k, p in KINDS]
_ANGLE = re.compile(r'(?:slope|roof|wedge)[^0-9]*?(\d{2})\b')
_BAD_DESC = re.compile(r'^[~=_|]')
_MOVED = re.compile(r'^~moved to\s+(\S+)', re.IGNORECASE)

NAME_ROLES = [   # words in section names/descriptions -> role (prior for generic parts only)
    ('roof', r'roof|gable|chimney|dormer|eave|attic|dome|ridge'),
    ('window', r'window|glazing|pane'),
    ('door', r'\bdoor|gate|entrance|portal'),
    ('wheel', r'wheel|tyre|tire'),
    ('chassis', r'chassis|undercarriage|underbody|frame|axle|suspension|bogie'),
    ('wing', r'\bwings?\b|aileron|stabili[sz]er|tailplane'),
    ('engine', r'engine|thruster|propeller|rotor|motor|turbine|exhaust|nacelle|reactor'),
    ('glass', r'canopy|cockpit|windscreen|windshield|glass'),
    ('base', r'baseplate|foundation|floor|ground|platform|\bbase\b|plinth|pavement'),
    ('wall', r'wall|facade|storey|story|shell|tower|keep|battlement|column|pillar'),
    ('body', r'body|cabin|\bcab\b|hull|fuselage|bonnet|hood|trunk|boot|bed\b|bucket|trailer|carriage|wagon'),
    ('detail', r'minifig|figure|furniture|interior|garden|tree|plant|lamp|sign|bench|decor|accessor|driver|pilot|crew|people'),
    ('trim', r'trim|fence|railing|stair|porch|balcony|awning|bumper|spoiler|mirror|grille'),
]
_NAME_ROLES = [(r, re.compile(p)) for r, p in NAME_ROLES]

DOMAIN_WORDS = {
    'vehicle': r'\b(car|truck|van|bus|lorry|tractor|motorcycle|motorbike|bike|jeep|pickup|taxi|limousine|racer|train|locomotive|'
               r'tram|vehicle|trailer|tank|digger|excavator|crane|boat|ship|yacht|submarine|ferry)\b',
    'aircraft': r'\b(plane|aeroplane|airplane|jet|helicopter|aircraft|glider|fighter|bomber|starfighter|spaceship|spacecraft|'
                r'starship|shuttle|rocket|ufo|drone|zeppelin|airship)\b',
    'building': r'\b(house|home|cottage|building|tower|castle|church|station|shop|store|school|hotel|barn|villa|lighthouse|'
                r'palace|museum|cafe|bank|library|mill|fort|temple|hut|cabin|garage|office|skyscraper)\b',
}


SPECIFIC = {'roof', 'window', 'door', 'wheel', 'wing', 'engine', 'glass'}   # names that are surer than geometry
NAMED_IN = {   # a "wing roof" of a cottage is no aircraft wing: a name only counts in a domain where that role exists
    'building': {'roof', 'window', 'door', 'glass'},
    'vehicle': {'wheel', 'engine', 'glass', 'window', 'door', 'roof'},
    'aircraft': {'wing', 'engine', 'glass', 'wheel', 'roof'},
    'generic': {'roof', 'window', 'door', 'wheel', 'wing', 'engine', 'glass'},
}


class PartFacts:
    """Description, kind and local bounding box of library parts, cached per library."""

    def __init__(self, parts):
        self.parts, self._cache = parts, {}

    def get(self, ref):
        code = ref.casefold().replace('\\', '/').removesuffix('.dat').split('/')[-1]
        hit = self._cache.get(code)
        if hit is None:
            try:
                desc = (self.parts.description_for(code) or '').strip()
                for _ in range(3):      # a "~Moved to 3023b" stub describes nothing: follow it to the real part
                    moved = _MOVED.match(desc)
                    if not moved:
                        break
                    desc = (self.parts.description_for(moved.group(1)) or '').strip()
            except Exception:  # noqa: BLE001 - an unreadable part just has no description
                desc = ''
            try:
                box = self.parts.bounding_box(code)
                bounds = np.array([[box.min.x, box.min.y, box.min.z], [box.max.x, box.max.y, box.max.z]], dtype=float)
            except Exception:  # noqa: BLE001
                bounds = np.array([[-10, 0, -10], [10, 24, 10]], dtype=float)
            hit = self._cache[code] = dict(code=code, desc=desc, kind=part_kind(desc), bounds=bounds)
        return hit


def part_kind(description):
    """Semantic kind of a part from its description ('slope', 'window', 'wheel', 'plate', ...); 'other' when unknown."""
    text = ' '.join(description.casefold().split())
    text = text.lstrip('=_~| ') if not text.startswith('~moved') else ''    # aliases and physical-colour parts
    if not text:
        return 'other'
    for kind, pattern in _KINDS:
        if pattern.search(text):
            return kind
    return 'other'


def slope_angle(description):
    found = _ANGLE.search(description.casefold())
    return int(found.group(1)) if found else None


# ------------------------------------------------------------------------------------------------- flattening

def _parse(text):
    """MPD text -> {section name: [(id, colour, position, matrix, ref)]} in file order, plus the main section's name."""
    sections, current, pending, main = {}, None, None, None
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == '0':
            if len(words) >= 3 and words[1] == 'FILE':
                name = line.split(None, 2)[2].strip()
                current = sections.setdefault(name, [])
                main = main or name
            elif len(words) >= 3 and words[1] == '//':
                pending = words[2].rstrip(':')
            continue
        if words[0] == '1' and len(words) >= 15 and current is not None:
            colour = int(words[1])
            nums = [float(v) for v in words[2:14]]
            current.append((pending, colour, np.array(nums[:3]), np.array(nums[3:]).reshape(3, 3), ' '.join(words[14:])))
            pending = None
    return sections, main


def flatten(plan, parts, facts=None):
    """Every physical part of the model with its world pose, effective colour and the submodel chain above it.

    Returns a list of dicts: key (section, id) of the placement in its own section, chain [(section, id), ...] from the
    root placement down to the part, ref, colour, bounds (world min/max, LDraw coordinates) and part facts.
    """
    facts = facts or PartFacts(parts)
    text = build_plan(plan, parts, validate=False)[0]
    sections, main = _parse(text)
    lookup = {name.casefold(): name for name in sections}
    out = []

    def walk(name, position, matrix, colour, chain, depth):
        if depth > 40:
            return
        for entry_id, c, pos, mat, ref in sections[name]:
            child = lookup.get(ref.casefold())
            world_pos, world_mat = position + matrix @ pos, matrix @ mat
            eff = colour if c in (16, 24) else c
            link = chain + [(name, entry_id)]
            if child is not None:
                walk(child, world_pos, world_mat, eff, link, depth + 1)
                continue
            info = facts.get(ref)
            lo, hi = info['bounds']
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            world = corners @ world_mat.T + world_pos
            out.append(dict(key=(name, entry_id), chain=link, ref=ref, colour=eff, facts=info, mat=world_mat, origin=world_pos,
                            bounds=np.array([world.min(axis=0), world.max(axis=0)])))
    if main is not None:
        walk(main, np.zeros(3), np.eye(3), 7, [], 0)
    return out


# ------------------------------------------------------------------------------------------------ classifier

def _transparent(parts, colour):
    entry = parts.colours_by_code.get(colour)
    return bool(entry and entry.is_transparent)


def _hint_domain(text):
    text = (text or '').casefold()
    return [d for d, pattern in DOMAIN_WORDS.items() if re.search(pattern, text)]


def _name_role(chain_text):
    for role, pattern in _NAME_ROLES:
        if pattern.search(chain_text):
            return role
    return None


def _chain_text(sections, chain, root):
    """Names of the submodels above a part, innermost first (the root model's own name says nothing about a part)."""
    return [re.sub(r'\.(ldr|mpd|dat)$', '', section.casefold()).replace('-', ' ').replace('_', ' ')
            for section, _ in reversed(chain) if section != root and section in sections][:2]


def classify(plan, parts, *, hints='', use_names=True, facts=None):
    """Role of every placement of the plan: {(section name, entry id): Role}.

    Submodel placements get the dominant role of what they contain. `hints` is free text about the model (title,
    request, description) used to pick the vocabulary (vehicle, aircraft, building). `use_names=False` ignores
    submodel names (the measurement of what geometry and parts alone can do). The result also has a '_domain' entry
    (building, vehicle, aircraft or generic) naming the vocabulary the scorer assumed.
    """
    facts = facts or PartFacts(parts)
    leaves = flatten(plan, parts, facts)
    if not leaves:
        return {}
    sections = {s['name']: s for s in plan['sections']}
    root = plan['sections'][0]
    hint_text = ' '.join([hints or '', root.get('description', ''), root['name']])
    transparent = [_transparent(parts, leaf['colour']) for leaf in leaves]

    # A scene (a house with a car in front of it, a cabin with a boat) is several objects: when the root's own
    # sub-assemblies are confidently of different domains, each one is scored in its own domain and frame.
    groups = collections.defaultdict(list)
    for i, leaf in enumerate(leaves):
        groups[leaf['chain'][0] if len(leaf['chain']) > 1 else None].append(i)
    big = [(link, idx) for link, idx in groups.items() if link is not None and len(idx) >= 12]
    objects = []
    for cluster in _clusters(leaves, [idx for _, idx in big]):     # sub-assemblies that touch form one object
        idx = [i for k in cluster for i in big[k][1]]
        text = ' '.join(re.sub(r'\.(ldr|mpd|dat)$', '', leaves[big[k][1][0]]['chain'][1][0].casefold()) + ' ' +
                        sections.get(leaves[big[k][1][0]]['chain'][1][0], {}).get('description', '') for k in cluster)
        found_domain, found_score = _domain([leaves[i] for i in idx], text)
        if found_score >= 3:
            objects.append((found_domain, idx))
    if len({d for d, _ in objects}) >= 2:
        taken = {i for _, idx in objects for i in idx}
        rest = [i for i in range(len(leaves)) if i not in taken]
        plan_groups = list(objects)
        if rest:
            plan_groups.append((_domain([leaves[i] for i in rest], hint_text)[0], rest))
    else:
        plan_groups = [(_domain(leaves, hint_text)[0], list(range(len(leaves))))]
    domain = max(plan_groups, key=lambda g: len(g[1]))[0]

    votes = collections.defaultdict(list)
    for group_domain, idx in plan_groups:
        found = _score_group([leaves[i] for i in idx], [transparent[i] for i in idx], group_domain, sections, root['name'],
                             use_names)
        for i, role in zip(idx, found, strict=True):
            votes[leaves[i]['key']].append(role)
    result = {}
    for key, found in votes.items():     # a part inside a submodel used several times is voted on by every instance
        ballot = collections.defaultdict(float)
        for r in found:
            ballot[r.role] += r.confidence
        best = max(ballot, key=ballot.get)
        voters = [r for r in found if r.role == best]
        top = max(voters, key=lambda r: r.confidence)
        result[key] = Role(best, round(sum(r.confidence for r in voters) / len(found), 3), top.reason)

    # Submodel instances: the dominant role of the leaves below (confidence-weighted), bottom-up over the chains.
    below = collections.defaultdict(lambda: collections.defaultdict(float))
    for leaf in leaves:
        role = result[leaf['key']]
        for link in leaf['chain'][:-1]:
            below[link][role.role] += role.confidence
    for link, ballot in below.items():
        best = max(ballot, key=ballot.get)
        result[link] = Role(best, round(ballot[best] / sum(ballot.values()), 3), 'submodel majority')
    result = {_entry_key(plan, k): v for k, v in result.items()}
    result['_domain'] = domain
    return result


def _clusters(leaves, groups):
    """Groups of parts (index lists) merged when their x/z boxes overlap by more than a fifth of the smaller one: a roof
    on its house or a cab on its chassis is one object; a car parked beside the house is another. Returns index lists
    into `groups`."""
    boxes = []
    for idx in groups:
        lo = np.min([leaves[i]['bounds'][0] for i in idx], axis=0)
        hi = np.max([leaves[i]['bounds'][1] for i in idx], axis=0)
        boxes.append((lo[[0, 2]], hi[[0, 2]]))
    parent = list(range(len(groups)))

    def find(k):
        while parent[k] != k:
            k = parent[k]
        return k
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            (alo, ahi), (blo, bhi) = boxes[a], boxes[b]
            inter = np.clip(np.minimum(ahi, bhi) - np.maximum(alo, blo), 0, None).prod()
            smaller = min((ahi - alo).prod(), (bhi - blo).prod())
            if smaller <= 0 or inter > 0.2 * smaller:
                parent[find(b)] = find(a)
    out = collections.defaultdict(list)
    for k in range(len(groups)):
        out[find(k)].append(k)
    return list(out.values())


def _domain(leaves, hint_text):
    """(domain, score) of a set of parts: building, vehicle, aircraft or generic, from part kinds and the hint text."""
    kinds = collections.Counter(leaf['facts']['kind'] for leaf in leaves)
    n = len(leaves)
    hinted = _hint_domain(hint_text)
    wheels = kinds['wheel'] + kinds['tyre']
    score = dict(
        aircraft=2 * (kinds['wing'] >= 2) + (kinds['wing'] >= 1) + (kinds['engine'] > 0) + 3 * ('aircraft' in hinted),
        vehicle=2 * (wheels >= 2) + (wheels >= 1) + 3 * ('vehicle' in hinted) + (kinds['vehicle'] >= 2)
        + 3 * any(leaf['facts']['desc'].casefold().startswith(('boat', 'hull')) for leaf in leaves),
        building=2 * (kinds['window'] + kinds['door'] + kinds['glass_window'] > 0) + 3 * ('building' in hinted)
        + (kinds['slope'] > 0.05 * n and not wheels))
    # Ties go to the vehicle (a car has wings), then the building (a plane has windows).
    domain = max(('vehicle', 'building', 'aircraft'), key=lambda d: score[d])
    return (domain if score[domain] >= 2 else 'generic'), score[domain]


def _score_group(leaves, transparent, domain, sections, root, use_names):
    """Role of each part of one object (the whole model, or one object of a scene), in the object's own frame."""
    # Model frame. Loose details (figures, plants, lamps) must not stretch the height band.
    solid = [lf for lf in leaves if lf['facts']['kind'] not in ('detail', 'light', 'baseplate')] or leaves
    lows = np.array([lf['bounds'][0] for lf in solid])
    highs = np.array([lf['bounds'][1] for lf in solid])
    ground = float(np.percentile(-highs[:, 1], 2))      # up = -y; lowest underside
    ceiling = float(np.percentile(-lows[:, 1], 98))
    height = max(ceiling - ground, 20.0)
    centre_xz = (lows[:, [0, 2]].min(axis=0) + highs[:, [0, 2]].max(axis=0)) / 2
    extent_xz = highs[:, [0, 2]].max(axis=0) - lows[:, [0, 2]].min(axis=0)
    span_axis = 0 if extent_xz[0] >= extent_xz[1] else 1                      # 0: x, 1: z; the wider direction
    half_span = max(extent_xz[span_axis] / 2, 20.0)
    footprint = max(extent_xz[0] * extent_xz[1], 1.0)

    def rel(leaf):
        lo, hi = leaf['bounds']
        bottom, top = -hi[1], -lo[1]
        return (bottom - ground) / height, (top - ground) / height, ((bottom + top) / 2 - ground) / height

    def share(leaf):
        lo, hi = leaf['bounds']
        return (hi[0] - lo[0]) * (hi[2] - lo[2]) / footprint

    def lateral(leaf):
        lo, hi = leaf['bounds']
        return abs((lo[[0, 2][span_axis]] + hi[[0, 2][span_axis]]) / 2 - centre_xz[span_axis]) / half_span

    # Anchors for context rules: where the roof starts, how high the wheels reach.
    roof_start = None
    slopes = [lf for lf in leaves if lf['facts']['kind'] == 'slope' and (slope_angle(lf['facts']['desc']) or 45) <= 55
              and 'inverted' not in lf['facts']['desc'].casefold() and rel(lf)[2] >= 0.4]
    if domain == 'building' and slopes:
        roof_start = min(rel(lf)[0] for lf in slopes)
    wheel_top = max((rel(lf)[1] for lf in leaves if lf['facts']['kind'] in ('wheel', 'tyre')), default=None)
    wheel_mid = (min(rel(lf)[0] for lf in leaves if lf['facts']['kind'] in ('wheel', 'tyre')) + wheel_top) / 2 \
        if wheel_top is not None else None
    # The top of the glazing (a vehicle's roof sits on it) and the half width of an aircraft's fuselage: the width of
    # its bricks and slopes near the centre line; a plate reaching well beyond it is a wing.
    glass_top = max((rel(lf)[1] for i, lf in enumerate(leaves)
                     if lf['facts']['kind'] in ('windscreen', 'glass_window') or (transparent[i] and share(lf) >= .01)), default=None)

    def edge(leaf):
        lo, hi = leaf['bounds']
        a = [0, 2][span_axis]
        return max(abs(lo[a] - centre_xz[span_axis]), abs(hi[a] - centre_xz[span_axis]))
    bulky = [edge(lf) for lf in leaves if lf['facts']['kind'] in ('brick', 'slope', 'vehicle', 'windscreen')]
    body_half = float(np.percentile(bulky, 40)) if bulky else None    # most bricks and slopes sit in the fuselage
    ctx = dict(roof_start=roof_start, wheel_top=wheel_top, wheel_mid=wheel_mid, glass_top=glass_top, body_half=body_half)

    return [_score(leaf, transparent[i], domain, rel(leaf), share(leaf), lateral(leaf), ctx, edge(leaf), sections, root,
                   use_names) for i, leaf in enumerate(leaves)]


def _entry_key(plan, key):
    """Map an expanded `repeat` id ('post-3') back to the plan entry that made it."""
    section, entry_id = key
    for s in plan['sections']:
        if s['name'] == section:
            ids = {p['id'] for step in s['steps'] for p in step}
            if entry_id not in ids and '-' in entry_id and entry_id.rsplit('-', 1)[0] in ids:
                return section, entry_id.rsplit('-', 1)[0]
    return key


def _score(leaf, glassy, domain, rel_h, share, lateral, ctx, edge, sections, root, use_names):
    info = leaf['facts']
    kind, desc = info['kind'], info['desc'].casefold()
    bottom, top, mid = rel_h
    # 1. parts that say what they are
    if kind == 'tyre':
        return Role('tyre', .95, 'tyre part')
    if kind == 'wheel':
        return Role('wheel', .95, 'wheel part')
    if kind == 'baseplate':
        return Role('base', .9, 'baseplate')
    if kind == 'detail':
        return Role('detail', .85, f'{desc[:30]} is a loose detail')
    if kind in ('glass_window',):
        return Role('glass' if domain in ('vehicle', 'aircraft') else 'window', .9, 'glass pane part')
    if kind == 'windscreen':
        return Role('glass', .9, 'windscreen/canopy part')
    if kind == 'window':
        return Role('window' if domain != 'aircraft' else 'glass', .9, 'window part')
    if kind == 'door':
        return Role('door', .9, 'door part')
    if kind == 'wing':
        return Role('wing' if domain == 'aircraft' else 'trim', .9, 'wing part')
    if kind == 'engine':
        return Role('engine', .8, 'engine part')
    if kind == 'light':
        return Role('detail', .6, 'light part')
    if glassy:
        small = share < 0.01
        if small and domain in ('vehicle', 'aircraft'):
            return Role('detail', .6, 'small transparent part (light)')
        return Role('window' if domain == 'building' else 'glass', .75, 'transparent colour')
    # 2. what the part's place in the model says, and 3. what the submodel it sits in is called; the surer one wins
    if domain == 'building':
        geo = _building(kind, desc, bottom, top, mid, share, ctx['roof_start'])
    elif domain == 'vehicle':
        geo = _vehicle(kind, desc, bottom, top, mid, share, ctx['wheel_top'], ctx['glass_top'], ctx['wheel_mid'])
    elif domain == 'aircraft':
        geo = _aircraft(kind, desc, bottom, top, mid, share, lateral, edge, ctx['body_half'])
    elif kind == 'technic':
        geo = Role('structure', .5, 'technic part')
    elif kind in ('slope', 'tile') and bottom > 0.6:
        geo = Role('body', .35, 'top surface')
    else:
        geo = Role('structure', .35, 'no context')
    if use_names:
        # Submodel names only: descriptions name half the model ("car body with wheels") and measured worse than nothing.
        for text in _chain_text(sections, leaf['chain'], root):
            named = _name_role(text)
            if named in SPECIFIC and named in NAMED_IN[domain] and .85 > geo.confidence:   # generic names measured worse than geometry
                return Role(named, .85, f'inside "{text[:40]}"')
    return geo


def _building(kind, desc, bottom, top, mid, share, roof_start):
    angle = slope_angle(desc) or 45
    if kind == 'slope':
        if 'inverted' in desc or angle > 60:
            return Role('wall' if mid < .6 else 'roof', .45, 'steep/inverted slope')
        if mid >= .4:
            return Role('roof', .85, 'slope in the upper half')
        return Role('trim', .45, 'low slope')
    if roof_start is not None and bottom >= roof_start - 0.02 and kind in ('plate', 'tile', 'wedgeplate', 'brick', 'other'):
        return Role('roof' if kind != 'brick' else 'wall', .6 if kind != 'brick' else .45, 'inside the roof zone')
    if kind in ('plate', 'tile', 'wedgeplate'):
        if bottom < .12 and share > .08:
            return Role('base', .7, 'wide plate at the bottom')
        if bottom < .02 and kind == 'plate':
            return Role('base', .55, 'plate in the bottom layer')
        if roof_start is None and top > .9 and (share > .05 or kind == 'tile'):
            return Role('roof', .6, 'flat top layer')
        return Role('trim', .5, 'plate or tile between courses')
    if kind == 'arch':
        return Role('wall', .6, 'arch')
    if kind == 'fence':
        return Role('trim', .55, 'fence, stair or support')
    if kind in ('brick', 'other', 'technic'):
        return Role('wall', .7 if kind == 'brick' else .45, 'brick in the wall band')
    return Role('structure', .35, 'no context')


def _vehicle(kind, desc, bottom, top, mid, share, wheel_top, glass_top=None, wheel_mid=None):
    if kind == 'vehicle':
        if re.search(r'bogie|buffer|coupling|chassis|axle|suspension|steering', desc):
            return Role('chassis', .7, 'running gear part')
        return Role('body', .8, 'vehicle body part')
    if kind == 'technic':
        return Role('chassis', .6, 'technic part')
    inside_wheels = wheel_top is not None and top <= wheel_top + 0.02
    flat = kind in ('plate', 'tile', 'wedgeplate', 'fence')
    # big wheels reach up the cab: only flat parts, or parts starting below the axles, are the chassis there
    if inside_wheels and (flat or (kind in ('brick', 'other') and (wheel_mid is None or bottom < wheel_mid))):
        return Role('chassis', .6, 'low, level with the wheels')
    if kind in ('slope', 'tile', 'plate', 'wedgeplate') and bottom > .62:
        return Role('roof', .55, 'top surface of the cabin')
    if glass_top is not None and glass_top < .97 and bottom >= glass_top - .02 and kind in ('brick', 'other'):
        return Role('roof', .5, 'above the glazing')
    if kind == 'fence':
        return Role('trim', .5, 'fitting')
    return Role('body', .55, 'above the wheels')


def _aircraft(kind, desc, bottom, top, mid, share, lateral, edge=0.0, body_half=None):
    if kind in ('wedgeplate',):
        return Role('wing', .6, 'wedge plate')
    if kind in ('plate', 'tile') and body_half is not None:
        if edge > body_half + 15:
            return Role('wing', .6, 'flat part reaching beyond the fuselage')
        return Role('body', .5, 'flat part within the fuselage width')
    if kind in ('plate', 'tile') and (lateral > .35 or mid < .4):
        return Role('wing', .55, 'flat part low in the model or far from the centre line')
    if kind == 'technic':
        return Role('structure', .5, 'technic part')
    return Role('body', .5, 'fuselage')


# ------------------------------------------------------------------------------------------------ plan tagging

def canonical(role):
    return CANONICAL.get(role, role)


def tag_plan(plan, parts, *, hints='', overwrite=False, use_names=True, facts=None):
    """Copy of the plan with a `role` on every placement that has none; returns (plan, report).

    The report lists per-role counts, the low-confidence placements and the domain the scorer assumed.
    """
    found = classify(plan, parts, hints=hints, use_names=use_names, facts=facts)
    domain = found.pop('_domain', 'generic') if found else 'generic'
    out = copy.deepcopy(plan)
    counts, low = collections.Counter(), []
    for section in out['sections']:
        for step in section['steps']:
            for entry in step:
                role = found.get((section['name'], entry['id']))
                if role is None:
                    continue
                if overwrite or 'role' not in entry:
                    entry['role'] = role.role
                counts[entry['role']] += 1
                if role.confidence < .5:
                    low.append(dict(section=section['name'], id=entry['id'], role=role.role, confidence=role.confidence))
    return out, dict(domain=domain, counts=dict(counts), low_confidence=low)


def role_of(plan):
    """{(section, id): role} of the roles already stored on the plan's placements."""
    return {(s['name'], p['id']): p['role'] for s in plan['sections'] for step in s['steps'] for p in step if p.get('role')}
