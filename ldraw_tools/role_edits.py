"""Edit primitives by role or selection: pure functions from a plan to a list of editor operations.

    from ldraw_tools import role_edits, editor
    ops = role_edits.recolour_role(plan, 'roof', 'blue')            # "make the roof blue", on ANY build with roles
    ops = role_edits.remove_role(plan, 'chimney')                     # "no chimney"
    ops = role_edits.recolour_selection(plan, ['p0012', 'p0013'], 'red')
    new_plan = editor.apply(plan, ops)                                # the editor stays the only thing that changes plans

The plan must carry roles (generators stamp them; `roles.tag_plan` adds them to anything else). The result is a list
(an editor-ready batch) with three extra attributes: `matched` (placements the edit meant), `skipped` (why some were
left alone) and `notes`. Nothing here touches geometry, positions or the plan itself.
"""
from __future__ import annotations

import collections

from .generators.colours import colour_code
from .roles import ROLE_WORDS, canonical

# Transparent LDraw colours that keep their transparency when a window or lamp is recoloured.
TRANSPARENT = {33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 57, 10375}
GLASSY_ROLES = {'glass', 'window', 'canopy'}


class Edit(list):
    """A list of editor ops plus what the edit matched and what it had to leave alone."""

    def __init__(self, ops=(), matched=(), skipped=(), notes=()):
        super().__init__(ops)
        self.matched, self.skipped, self.notes = list(matched), list(skipped), list(notes)


def role_word(word):
    """Role for what a person says ('roof', 'windows', 'tyres', 'it' -> None); also accepts a role name."""
    word = (word or '').strip().casefold()
    return ROLE_WORDS.get(word) or (word if word in set(ROLE_WORDS.values()) else None)


def _role(plan, word):
    """The role a word means on this plan: a role the plan really has wins ('chimney'), else the vocabulary ('roofs')."""
    word = (word or '').strip().casefold()
    return word if any(e.get('role') == word for _, e in _entries(plan)) else (role_word(word) or word)


def _entries(plan):
    for section in plan['sections']:
        for step in section['steps']:
            for entry in step:
                yield section, entry


def _role_targets(plan, role, chosen=None):
    """(section, id) of every placement with exactly this role; when none has it, those whose generator role folds into it
    (an 'accent' or 'hull' placement answers to 'trim' and 'body')."""
    def pick(match):
        return {(s['name'], e['id']) for s, e in _entries(plan) if e.get('role') and match(e['role'])
                and (chosen is None or (s['name'], e['id']) in chosen)}
    return pick(lambda r: r == role) or pick(lambda r: canonical(r) == role)


def _select(plan, selection):
    """Set of (section name, id) the selection names; a bare id means that id in the root section, else everywhere."""
    if selection is None:
        return None
    root = plan['sections'][0]['name']
    chosen, by_id = set(), collections.defaultdict(set)
    for section, entry in _entries(plan):
        by_id[entry['id']].add(section['name'])
    for item in selection:
        if isinstance(item, dict):
            chosen.add((item.get('section') or root, item['id']))
        elif isinstance(item, (tuple, list)):
            chosen.add((item[0], item[1]))
        elif ':' in str(item) and str(item).split(':', 1)[0] in {s['name'] for s in plan['sections']}:
            chosen.add(tuple(str(item).split(':', 1)))
        else:
            where = by_id.get(str(item), set())
            chosen |= {(root if root in where else name, str(item)) for name in (where if root not in where else {root})}
    return chosen


def roles_of_selection(plan, selection):
    """Roles carried by the selected placements, most common first."""
    chosen = _select(plan, selection)
    count = collections.Counter(e['role'] for s, e in _entries(plan) if e.get('role') and (s['name'], e['id']) in chosen)
    return [r for r, _ in count.most_common()]


def role_summary(plan):
    """{role: {'count': n, 'colours': {code: n}}} of the roles on the plan (leaf parts, not submodel instances)."""
    sections = {s['name'].casefold() for s in plan['sections']}
    out = {}
    for _, entry in _entries(plan):
        if entry['ref'].casefold() in sections:
            continue
        info = out.setdefault(entry.get('role', 'untagged'), dict(count=0, colours=collections.Counter()))
        info['count'] += 1
        info['colours'][entry['colour']] += 1
    return {r: dict(count=i['count'], colours=dict(i['colours'].most_common())) for r, i in sorted(out.items())}


def _colour_for(entry, value, role, parts=None):
    glass = role in GLASSY_ROLES or entry['colour'] in TRANSPARENT
    if parts is not None and isinstance(entry['colour'], int) and entry['colour'] in parts.colours_by_code:
        glass = role in GLASSY_ROLES or parts.colours_by_code[entry['colour']].is_transparent
    return colour_code(value, glass=glass)


def _recolour(plan, targets, value, role, parts, notes):
    """Ops recolouring the target (section, id) pairs; parts that inherit their colour are recoloured via their parent."""
    ops, matched, skipped = [], [], []
    sections = {s['name']: s for s in plan['sections']}
    leaves = {(s['name'], e['id']): e for s, e in _entries(plan)}
    instances = collections.defaultdict(list)     # section name -> placements (section, id) that instantiate it
    for s, e in _entries(plan):
        for name in sections:
            if e['ref'].casefold() == name.casefold():
                instances[name].append((s['name'], e['id']))
    inherit = collections.defaultdict(list)
    for key in sorted(targets):
        entry = leaves[key]
        if entry['ref'].casefold() in {n.casefold() for n in sections}:
            continue    # a submodel instance is recoloured through its parts
        colour = _colour_for(entry, value, role, parts)
        if entry['colour'] == 16 and key[0] != plan['sections'][0]['name']:
            inherit[key[0]].append(key)         # takes the colour of whatever instantiates its section
            continue
        matched.append(key)
        if entry['colour'] != colour:
            ops.append(dict(op='recolour', id=key[1], section=key[0], colour=colour))
    for name, keys in inherit.items():
        inheriting = [(s['name'], e['id']) for s, e in _entries(plan) if s['name'] == name and e['colour'] == 16]
        if set(inheriting) <= set(keys):        # every inheriting part is a target: colour the instances instead
            for parent in instances.get(name, []):
                colour = _colour_for(leaves[keys[0]], value, role, parts)
                matched.extend(keys)
                if leaves[parent]['colour'] != colour:
                    ops.append(dict(op='recolour', id=parent[1], section=parent[0], colour=colour))
        else:
            skipped.extend((k, 'inherits its colour together with parts of another role') for k in keys)
            notes.append(f'{len(keys)} part(s) in {name} take their colour from the parent and share it with other roles; left as they are')
    return Edit(ops, matched, skipped, notes)


def recolour_role(plan, role, colour, *, selection=None, parts=None):
    """Ops that give every placement of `role` (optionally only within `selection`) a colour: name, code or symbol.

    A colour name for a window/glass role resolves to the transparent variant (blue glass is Trans_Blue).
    """
    role = _role(plan, role)
    chosen = _select(plan, selection)
    targets = _role_targets(plan, role, chosen)
    notes = [] if targets else [f'no placement has the role {role!r}; roles present: {", ".join(role_summary(plan))}']
    return _recolour(plan, targets, colour, role, parts, notes)


def recolour_selection(plan, selection, colour, *, parts=None):
    """Ops that recolour exactly the selected placements (a submodel instance recolours the parts that inherit)."""
    chosen = _select(plan, selection)
    by_key = {(s['name'], e['id']): e for s, e in _entries(plan)}
    unknown = [k for k in chosen if k not in by_key]
    sections = {s['name'].casefold() for s in plan['sections']}
    ops, matched, notes = [], [], [f'unknown selection {k}' for k in unknown]
    for key in sorted(chosen - set(unknown)):
        entry = by_key[key]
        value = _colour_for(entry, colour, entry.get('role'), parts)
        if entry['ref'].casefold() in sections:
            notes.append(f'{key[1]} is a sub-assembly: only its parts that inherit their colour change')
        matched.append(key)
        if entry['colour'] != value:
            ops.append(dict(op='recolour', id=key[1], section=key[0], colour=value))
    return Edit(ops, matched, (), notes)


def recolour_like_selection(plan, selection, colour, *, parts=None):
    """"Make this roof blue": recolour every placement that has the role of the selection's most common role."""
    found = roles_of_selection(plan, selection)
    if not found:
        return Edit(notes=['the selection has no roles; run roles.tag_plan first or recolour the selection itself'])
    edit = recolour_role(plan, found[0], colour, parts=parts)
    edit.notes.insert(0, f'role of the selection: {found[0]}')
    return edit


def _removal(plan, targets):
    """Ops deleting the targets, refusing what would break the plan (dependants) or empty a section."""
    by_key = {(s['name'], e['id']): e for s, e in _entries(plan)}
    root = plan['sections'][0]['name']
    skipped, notes = [], []
    targets = {k for k in targets if k in by_key}
    # Placements that other placements depend on (on / attach.to / snap.to) stay unless the dependants go too.
    changed = True
    while changed:
        changed = False
        for s, e in _entries(plan):
            key = (s['name'], e['id'])
            if key in targets:
                continue
            needs = {e.get('on'), (e.get('attach') or {}).get('to'), (e.get('snap') or {}).get('to')} - {None}
            blocked = {(s['name'], n) for n in needs} & targets
            for b in blocked:
                targets.discard(b)
                skipped.append((b, f'{key[1]} is positioned relative to it'))
                changed = True
    # A section left empty cannot exist: drop its instances instead (the section stays, unused), unless it is the root.
    ops, emptied = [], set()
    for section in plan['sections']:
        mine = {(section['name'], e['id']) for step in section['steps'] for e in step}
        if mine and mine <= targets:
            if section['name'] == root:
                notes.append('the edit would remove every part of the model; nothing removed')
                return Edit([], [], [(k, 'whole model') for k in targets], notes)
            emptied.add(section['name'])
    for name in emptied:
        for s, e in _entries(plan):
            if e['ref'].casefold() == name.casefold():
                targets.add((s['name'], e['id']))
        targets -= {(name, e['id']) for step in next(x for x in plan['sections'] if x['name'] == name)['steps'] for e in step}
    for key in sorted(targets):
        ops.append(dict(op='delete', id=key[1], section=key[0]))
    if emptied:
        notes.append('sub-assemblies emptied by the edit were removed with their instances: ' + ', '.join(sorted(emptied)))
    return Edit(ops, sorted(targets), skipped, notes)


def remove_role(plan, role, *, selection=None):
    """Ops that delete every placement of `role` (optionally only within `selection`)."""
    role = _role(plan, role)
    chosen = _select(plan, selection)
    sections = {s['name'].casefold() for s in plan['sections']}
    targets = {k for k in _role_targets(plan, role, chosen)
               if next(e for s, e in _entries(plan) if (s['name'], e['id']) == k)['ref'].casefold() not in sections}
    edit = _removal(plan, targets)
    if not targets:
        edit.notes.append(f'no placement has the role {role!r}; roles present: {", ".join(role_summary(plan))}')
    return edit


def remove_selection(plan, selection):
    """Ops that delete exactly the selected placements."""
    return _removal(plan, set(_select(plan, selection)))


def apply_with_connectivity_note(plan, edit):
    """Plain `editor.apply` plus a note when a removal may leave parts unsupported (a cheap structural hint)."""
    from . import editor
    return editor.apply(plan, list(edit))
