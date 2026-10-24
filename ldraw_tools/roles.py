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
         'trim', 'detail', 'structure')

# Roles that the generators stamp, folded into the classifier vocabulary (used to score the classifier on them).
CANONICAL = {'porch': 'trim', 'chimney': 'roof', 'accent': 'trim', 'light': 'detail', 'hull': 'body',
             'canopy': 'glass', 'wood': 'detail', 'metal': 'trim', 'headlamp': 'detail', 'tail_lamp': 'detail'}

ROLE_WORDS = {   # what a person says -> role (used by role_edits to resolve "the roof", "the windows", "the wheels")
    'roof': 'roof', 'roofs': 'roof', 'chimney': 'roof', 'wall': 'wall', 'walls': 'wall', 'window': 'window',
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
               r'head|hips|legs?|arm|hand|neckwear|headwear|hipwear|footwear|stud|display|tag|ring|electric|train (track|wheel)|'
               r'antenna|crane hook|barrel|bucket|chest|treasure|sword|spear|shield|weapon|gun|ladder|balloon|belt|backpack)'),
    ('tyre', r'^(tyre|tire)'),
    ('wheel', r'^wheel(?!.*\barch\b)|wheel rim|\brim\b'),
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

    def _description(self, code):
        try:
            return (self.parts.description_for(code) or '').strip()
        except Exception:  # noqa: BLE001 - an unreadable part just has no description
            return ''

    def get(self, ref):
        code = ref.casefold().replace('\\', '/').removesuffix('.dat').split('/')[-1]
        hit = self._cache.get(code)
        if hit is None:
            desc = self._description(code)
            for _ in range(3):        # '~Moved to 3023b': the description (and so the family) is the target's
                moved = re.match(r'~Moved to (\S+)', desc)
                if not moved:
                    break
                desc = self._description(moved.group(1).casefold().removesuffix('.dat')) or desc
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
    if not text or _BAD_DESC.match(text):
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
    kinds = collections.Counter(leaf['facts']['kind'] for leaf in leaves)
    n = len(leaves)
    transparent = {i: _transparent(parts, leaf['colour']) for i, leaf in enumerate(leaves)}
    hinted = _hint_domain(hint_text)

    wheels = kinds['wheel'] + kinds['tyre']
    score = dict(
        aircraft=2 * (kinds['wing'] >= 2) + (kinds['wing'] >= 1) + (kinds['engine'] > 0) + 3 * ('aircraft' in hinted),
        vehicle=2 * (wheels >= 2) + (wheels >= 1) + 3 * ('vehicle' in hinted) + (kinds['vehicle'] >= 2),
        building=2 * (kinds['window'] + kinds['door'] + kinds['glass_window'] > 0) + 3 * ('building' in hinted)
        + (kinds['slope'] > 0.05 * n and not wheels))
    # Ties go to the vehicle (a car has wings), then the building (a plane has windows).
    domain = max(('vehicle', 'building', 'aircraft'), key=lambda d: score[d])
    if score[domain] < 2:
        domain = 'generic'

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

    votes = collections.defaultdict(list)
    for i, leaf in enumerate(leaves):
        votes[leaf['key']].append(_score(leaf, transparent[i], domain, rel(leaf), share(leaf), lateral(leaf), roof_start,
                                         wheel_top, sections, root['name'], use_names))
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


def _entry_key(plan, key):
    """Map an expanded `repeat` id ('post-3') back to the plan entry that made it."""
    section, entry_id = key
    for s in plan['sections']:
        if s['name'] == section:
            ids = {p['id'] for step in s['steps'] for p in step}
            if entry_id not in ids and '-' in entry_id and entry_id.rsplit('-', 1)[0] in ids:
                return section, entry_id.rsplit('-', 1)[0]
    return key


def _score(leaf, glassy, domain, rel_h, share, lateral, roof_start, wheel_top, sections, root, use_names):
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
        return Role('door' if domain == 'building' or domain == 'generic' else 'body', .9, 'door part')
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
        geo = _building(kind, desc, bottom, top, mid, share, roof_start)
    elif domain == 'vehicle':
        geo = _vehicle(kind, desc, bottom, top, mid, share, wheel_top)
    elif domain == 'aircraft':
        geo = _aircraft(kind, desc, bottom, top, mid, share, lateral)
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
        if top > .9 and share > .05 and roof_start is None:
            return Role('roof', .6, 'flat top layer')
        return Role('trim', .5, 'plate or tile between courses')
    if kind == 'arch':
        return Role('wall', .6, 'arch')
    if kind == 'fence':
        return Role('trim', .55, 'fence, stair or support')
    if kind in ('brick', 'other', 'technic'):
        return Role('wall', .7 if kind == 'brick' else .45, 'brick in the wall band')
    return Role('structure', .35, 'no context')


def _vehicle(kind, desc, bottom, top, mid, share, wheel_top):
    if kind == 'vehicle':
        return Role('body', .8, 'vehicle body part')
    if kind == 'technic':
        return Role('chassis', .6, 'technic part')
    inside_wheels = wheel_top is not None and top <= wheel_top + 0.02
    if inside_wheels and kind in ('plate', 'tile', 'brick', 'wedgeplate', 'other', 'fence'):
        return Role('chassis', .6, 'low, level with the wheels')
    if kind in ('slope', 'tile', 'plate', 'wedgeplate') and bottom > .62:
        return Role('roof', .55, 'top surface of the cabin')
    if kind == 'fence':
        return Role('trim', .5, 'fitting')
    return Role('body', .55, 'above the wheels')


def _aircraft(kind, desc, bottom, top, mid, share, lateral):
    if kind in ('wedgeplate',):
        return Role('wing', .6, 'wedge plate')
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
