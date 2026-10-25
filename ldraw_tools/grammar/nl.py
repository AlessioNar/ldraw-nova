"""Natural-language addressing: short phrases -> config patches. Rule based and deterministic (no model call).

"add a tower at each corner", "arched windows", "three storeys", "make the roof blue" each map to a few config
fields. apply_text returns the new config plus what it understood and what it did not.
"""
from __future__ import annotations

import re

from .colours import NAMES
from .roles import ALIASES
from .schema import deep_merge

NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'single': 1, 'double': 2, 'triple': 3}
COLOUR_WORDS = sorted((n for n in NAMES if n not in ('clear', 'transparent', 'trans clear')), key=lambda n: (-len(n), n))
ROLE_COLOUR = {
    'roof': ('roof', 'colour'), 'wall': ('walls', 'colour'), 'window': ('openings', 'frame_colour'),
    'door': ('openings', 'door_colour'), 'glass': ('openings', 'glass_colour'), 'base': ('base', 'colour'),
    'tower': ('corners', 'tower_colour'), 'trim': ('storeys', 'band_colour'), 'detail': ('details', 'chimney_colour'),
}
SPECIAL_WORDS = {'chimney': ('details', 'chimney_colour'), 'porch': ('details', 'porch_colour'),
                 'balcony': ('details', 'balcony_colour'), 'path': ('base', 'path_colour'),
                 'grass': ('base', 'colour'), 'ground': ('base', 'colour'), 'sign': ('details', 'sign_colour'),
                 'plinth': ('walls', 'plinth_colour'), 'battlements': ('roof', 'crenel_colour'),
                 'pillars': ('corners', 'pillar_colour'), 'timber': ('walls', 'timber_colour')}


def number(text):
    m = re.search(r'\b(\d+|' + '|'.join(NUMBERS) + r')\b', text)
    if not m:
        return None
    return int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1)]


def find_colour(text):
    for name in COLOUR_WORDS:
        if re.search(r'\b' + re.escape(name) + r'\b', text):
            return name
    return None


def colour_target(text):
    """Which colour parameter a phrase like 'make the roof blue' talks about."""
    for word, target in SPECIAL_WORDS.items():
        if re.search(r'\b' + word + r'\b', text):
            return target
    best = None
    for role, words in ALIASES.items():
        for w in words:
            m = re.search(r'\b' + re.escape(w) + r'\b', text)
            if m and role in ROLE_COLOUR and (best is None or m.start() < best[0]):
                best = (m.start(), ROLE_COLOUR[role])
    return best[1] if best else None


def clause_patch(text):
    """Config patch for one clause, or None if nothing is understood."""
    t = text.lower().strip().rstrip('.')
    patch = {}
    n = number(t)
    if re.search(r'\b(storey|storeys|story|stories|floor|floors)\b', t) and n:
        patch['storeys'] = {'count': max(1, min(6, n))}
    if re.search(r'\b(tower|towers|turret|turrets)\b', t) and not re.search(r'\b(no|remove|without)\b', t):
        c = {'style': 'towers'}
        if 'round' in t:
            c['tower_shape'] = 'round'
        if 'square' in t:
            c['tower_shape'] = 'square'
        if re.search(r'each corner|every corner|all corners|four corners|4 corners', t):
            c['tower_at'] = 'all'
        elif 'front' in t:
            c['tower_at'] = 'front'
        elif 'back' in t:
            c['tower_at'] = 'back'
        elif 'diagonal' in t or 'two corners' in t or 'opposite' in t:
            c['tower_at'] = 'diagonal'
        if re.search(r'\bcone|conical|pointed|pyramid\b', t):
            c['tower_roof'] = 'cone'
        patch['corners'] = c
    elif re.search(r'\b(no|remove|without)\b.*\btowers?\b', t):
        patch['corners'] = {'style': 'plain'}
    if re.search(r'\bpillars|quoins\b', t):
        patch.setdefault('corners', {})['style'] = 'pillars'
    for word, kind in (('arched', 'arch'), ('arch', 'arch'), ('arrow slit', 'slit'), ('slits', 'slit'), ('slit', 'slit'),
                       ('wide window', 'wide'), ('tall window', 'tall'), ('small window', 'small'), ('big window', 'rect'),
                       ('large window', 'rect')):
        if word in t and 'window' in t or word in ('arrow slit', 'slits', 'slit') and word in t:
            ground = re.search(r'ground|downstairs|lower|shop', t)
            upper = re.search(r'upper|upstairs|top', t)
            w = {}
            if not upper:
                w['ground_windows'] = kind
            if not ground:
                w['upper_windows'] = kind
            patch['openings'] = {**patch.get('openings', {}), **w}
            break
    if re.search(r'\bno windows\b', t):
        patch['openings'] = {**patch.get('openings', {}), 'ground_windows': 'none', 'upper_windows': 'none'}
    if re.search(r'\bwindows? (on|at) the (front|back|left|right)( only)?\b', t):
        sides = re.findall(r'\b(front|back|left|right)\b', t)
        patch['openings'] = {**patch.get('openings', {}), 'sides': sorted(set(sides))}
    if re.search(r'\bno door\b', t):
        patch['openings'] = {**patch.get('openings', {}), 'door': 'none'}
    m = re.search(r'\bdoor (on|at) the (left|right|centre|center)\b', t)
    if m:
        patch['openings'] = {**patch.get('openings', {}), 'door': 'plain',
                             'door_position': 'centre' if m.group(2) == 'center' else m.group(2)}
    for style, key in (('flat roof', 'flat'), ('gable', 'gable'), ('hip roof', 'hip'), ('hipped', 'hip'), ('no roof', 'none')):
        if style in t:
            patch['roof'] = {**patch.get('roof', {}), 'style': key}
    if re.search(r'battlement|crenellat|castle', t):
        patch['roof'] = {**patch.get('roof', {}), 'style': 'flat', 'parapet': 'battlement'}
    if re.search(r'\bparapet\b', t) and 'battlement' not in t:
        patch['roof'] = {**patch.get('roof', {}), 'style': 'flat', 'parapet': 'plain'}
    m = re.search(r'\bchimney( on the)? ?(left|right|centre|center)?\b', t)
    if m and not re.search(r'no chimney|remove', t):
        side = m.group(2) or 'right'
        patch['details'] = {**patch.get('details', {}), 'chimney': 'centre' if side == 'center' else side}
    if re.search(r'no chimney|remove the chimney', t):
        patch['details'] = {**patch.get('details', {}), 'chimney': 'none'}
    for word in ('porch', 'balcony'):
        if re.search(r'\b' + word + r'\b', t):
            on = not re.search(r'\b(no|remove|without)\b', t)
            patch['details'] = {**patch.get('details', {}), word: on}
    if re.search(r'\bsign\b', t) and not re.search(r'\b(no|remove)\b', t) and not find_colour(t):
        patch['details'] = {**patch.get('details', {}), 'sign': 'fascia'}
    for word, shape in (('l-shaped', 'L'), ('l shaped', 'L'), ('u-shaped', 'U'), ('u shaped', 'U'), ('courtyard', 'courtyard')):
        if word in t:
            patch['footprint'] = {'shape': shape}
    m = re.search(r'(\d+)\s*(?:x|by)\s*(\d+)', t)
    if m:
        patch['footprint'] = {**patch.get('footprint', {}), 'width': int(m.group(1)), 'depth': int(m.group(2))}
    m = re.search(r'(\d+)\s*studs?\s*(wide|long|deep)', t)
    if m:
        patch['footprint'] = {**patch.get('footprint', {}), ('depth' if m.group(2) == 'deep' else 'width'): int(m.group(1))}
    for word, key in (('half-timbered', 'timber'), ('half timbered', 'timber'), ('timber', 'timber'), ('stone', 'stone'),
                      ('brick walls', 'brick')):
        rest = t.replace(word, '')
        if word in t and (word.startswith('half') or not find_colour(rest) or re.search(r'\bwalls?\b', t)):
            patch['walls'] = {**patch.get('walls', {}), 'style': key}
            break
    if re.search(r'thick(er)? walls|double walls', t):
        patch['walls'] = {**patch.get('walls', {}), 'thickness': 2}
    if re.search(r'thin walls', t):
        patch['walls'] = {**patch.get('walls', {}), 'thickness': 1}
    if re.search(r'\bplinth\b', t) and not find_colour(t):
        patch['walls'] = {**patch.get('walls', {}), 'plinth': True}
    if re.search(r'\bcornice|projecting bands?\b', t):
        patch['storeys'] = {**patch.get('storeys', {}), 'band_projection': 1}
    colour = find_colour(t)
    if colour and not (patch.get('walls', {}).get('style') in ('stone',) and 'wall' not in t):
        target = colour_target(t)
        if target is None and re.search(r'\bmake it\b|\bpaint\b', t):
            target = ROLE_COLOUR['wall']
        if target is None and 'glass' in t:
            target = ROLE_COLOUR['glass']
        if target:
            patch.setdefault(target[0], {})[target[1]] = colour
    return patch or None


def apply_text(config, text):
    """(new config, understood clauses, unmatched clauses). The input config is not modified."""
    out, understood, missed = dict(config or {}), [], []
    for clause in re.split(r'[.;]|,\s*(?=\w+ )|\band\b|\bthen\b', text):
        clause = clause.strip()
        if not clause:
            continue
        patch = clause_patch(clause)
        if patch:
            out = deep_merge(out, patch)
            understood.append(dict(text=clause, patch=patch))
        else:
            missed.append(clause)
    return out, understood, missed
