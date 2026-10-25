"""Per-colour sub-roles: a role striped in several colours keeps one sub-role per colour group (issue #220).

A striped wall is `wall:stripe-a`, `wall:stripe-b`, ... (ontology/roles.ttl, `role_vocab.SUBROLES`), in the order the
colours first appear in the plan. The groups are found deterministically from the colours the plan already has; nothing
here guesses a role. A part the person corrected by hand is never moved, and a role with a single colour is left alone,
so a model without stripes behaves exactly as before.

    striped = subroles.split_stripes(tagged_plan)          # copy with `role: 'wall:stripe-a'` on the stripe parts
"""
from __future__ import annotations

import collections
import copy
import re

from . import role_vocab

MIN_PARTS = 2          # a colour with fewer parts than this is an accent, not a stripe
MIN_SHARE = 0.10       # ... and so is one with a smaller share of the role's coloured parts
INHERIT = (16, 24)     # colours that come from the parent: the stripe colour is not known here
STRIPE_WORDS = re.compile(r'^(?P<role>.+?)\s+stripes?(?:\s+(?P<which>[a-c]|first|second|third|1|2|3))?$')
WHICH = {'a': 0, 'b': 1, 'c': 2, 'first': 0, 'second': 1, 'third': 2, '1': 0, '2': 1, '3': 2}


def param_name(role):
    """Template parameter that sets a role's colour: colour_wall, colour_wall_stripe_a."""
    return 'colour_' + re.sub(r'[^a-z0-9]+', '_', role.casefold()).strip('_')


def label(role):
    """Readable name of a role or sub-role ('wall stripe A')."""
    parent = role_vocab.SUBROLES.get(role)
    return f"{parent} stripe {role.rsplit('-', 1)[-1].upper()}" if parent else role


def _entries(plan):
    sections = {s['name'].casefold() for s in plan['sections']}
    for section in plan['sections']:
        for step in section['steps']:
            for entry in step:
                if entry['ref'].casefold() not in sections:      # leaf parts only, not submodel instances
                    yield section, entry


def split_stripes(plan, *, keep=(), transparent=()):
    """Copy of a role-tagged plan with sub-roles stamped on the colour groups of every striped role.

    keep: (section, id) pairs the person corrected, which stay as they are. transparent: colour codes that never form a
    stripe (glass). A role already carrying a sub-role on any part is left to whoever put it there.
    """
    out = copy.deepcopy(plan)
    keep = set(keep)
    for parent in sorted({p for p in role_vocab.SUBROLES.values()}):
        names = role_vocab.subroles_of(parent)
        members = [(s['name'], e) for s, e in _entries(out) if e.get('role') == parent and (s['name'], e['id']) not in keep]
        if any(role_vocab.SUBROLES.get(e.get('role')) == parent for _, e in _entries(out)):
            continue
        coloured = [(k, e) for k, e in members if e['colour'] not in INHERIT and e['colour'] not in transparent]
        count = collections.Counter(e['colour'] for _, e in coloured)
        floor = max(MIN_PARTS, MIN_SHARE * len(coloured))
        groups = [c for c, n in count.most_common(len(names)) if n >= floor]
        if len(groups) < 2:
            continue
        first = {}
        for _, e in coloured:
            first.setdefault(e['colour'], len(first))
        groups.sort(key=first.get)                 # stripe-a is the colour that appears first
        for _, e in coloured:
            if e['colour'] in groups:
                e['role'] = names[groups.index(e['colour'])]
    return out


def stripes_of(plan):
    """{role: [sub-role, ...]} of the striped roles a plan carries, sub-roles in their fixed order."""
    found = collections.defaultdict(set)
    for _, entry in _entries(plan):
        parent = role_vocab.SUBROLES.get(entry.get('role'))
        if parent:
            found[parent].add(entry['role'])
    return {p: sorted(s) for p, s in sorted(found.items())}


def colour_list(value):
    """['red', 'white'] from 'red and white', 'red, white and blue' or a list; one colour gives a one-item list."""
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None]
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r',|\band\b|&|/', value) if p.strip()]
        return parts or [value]
    return [value]


def parse_stripe_word(word):
    """('wall', None) for 'wall stripes', ('wall', 0) for 'wall stripe a'; None when the word is not about stripes."""
    found = STRIPE_WORDS.match((word or '').strip().casefold())
    return (found.group('role'), WHICH.get(found.group('which'))) if found else None
