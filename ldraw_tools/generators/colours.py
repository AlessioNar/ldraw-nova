"""Colour names to LDraw codes, so a spec like {"body": "dark grey"} needs no catalog lookup or LLM."""
from __future__ import annotations

import hashlib

# Common names (and spellings) -> LDraw colour code. Codes are checked against the catalog in the tests.
NAMES = {
    'black': 0, 'blue': 1, 'green': 2, 'dark turquoise': 3, 'red': 4, 'dark pink': 5, 'brown': 70,
    'light grey': 71, 'light bluish grey': 71, 'grey': 71, 'silver': 71,
    'dark grey': 72, 'dark bluish grey': 72, 'charcoal': 72,
    'light blue': 9, 'bright green': 10, 'light turquoise': 11, 'turquoise': 11, 'salmon': 12, 'pink': 13,
    'yellow': 14, 'white': 15, 'light green': 17, 'light yellow': 18, 'tan': 19, 'beige': 19,
    'light violet': 20, 'purple': 22, 'orange': 25, 'magenta': 26, 'lime': 27, 'dark tan': 28,
    'bright pink': 29, 'medium lavender': 30, 'lavender': 31,
    'reddish brown': 70, 'medium blue': 73, 'sky blue': 322, 'azure': 322, 'medium azure': 322,
    'dark azure': 321, 'navy': 272, 'navy blue': 272, 'dark blue': 272, 'dark green': 288,
    'dark red': 320, 'maroon': 320, 'burgundy': 320, 'dark orange': 484, 'rust': 484,
    'dark brown': 308, 'lilac': 85, 'medium lilac': 85, 'medium green': 74, 'olive green': 330, 'olive': 330,
    'sand green': 378, 'sand blue': 379, 'sand red': 335, 'flesh': 92, 'nougat': 92, 'light brown': 86,
    'bright light orange': 191, 'bright light yellow': 226, 'cream': 226, 'bright light blue': 212,
    'gold': 334, 'pearl gold': 297, 'chrome': 383, 'pearl silver': 179,
    'clear': 47, 'transparent': 47, 'trans clear': 47, 'trans black': 10375, 'smoke': 10375, 'tinted': 10375,
    'trans red': 36, 'trans green': 34, 'trans blue': 33, 'trans dark blue': 33, 'trans light blue': 43,
    'trans yellow': 46, 'trans orange': 57,
}
# Glass and canopy roles take transparent codes: "blue glass" means Trans_Blue, not opaque blue.
GLASS = {'clear': 47, 'black': 10375, 'red': 36, 'green': 34, 'blue': 33, 'yellow': 46, 'orange': 57,
         'light blue': 43, 'grey': 10375, 'dark grey': 10375, 'white': 47}


def colour_code(value, *, glass=False):
    """LDraw code from a name ('Dark Grey', 'dark-grey', 'gray'), an integer code or '@colours.Name'."""
    if isinstance(value, bool):
        raise ValueError(f'Not a colour: {value!r}')
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Not a colour: {value!r}')
    text = value.strip().casefold().replace('-', ' ').replace('_', ' ').replace('gray', 'grey')
    text = ' '.join(text.removeprefix('@colours.').split())
    if text.isdigit():
        return int(text)
    if glass and text in GLASS:
        return GLASS[text]
    if text in NAMES:
        return NAMES[text]
    if text.startswith('bright ') and text[7:] in NAMES:
        return NAMES[text[7:]]
    raise ValueError(f'Unknown colour {value!r}; examples: red, white, dark grey, light blue, dark green, tan')


def colour_names():
    """One primary name per code, for prompts and forms."""
    seen, out = set(), []
    for name, code in NAMES.items():
        if code not in seen:
            seen.add(code)
            out.append(name)
    return out


def custom_palette(base_roles, **named):
    """Vehicle palette with some roles replaced by colour names; returns (palette name, roles).

    The name is a hash of the final roles, so the same request always yields the same plan. It is
    registered in vehicle_details.CUSTOM_PALETTES so every nested module sees the same colours.
    """
    from ..vehicle_details import CUSTOM_PALETTES
    roles = dict(base_roles)
    for role, value in named.items():
        if value is not None:
            roles[role] = colour_code(value, glass=role == 'glass')
    name = 'custom-' + hashlib.sha1(repr(sorted(roles.items())).encode()).hexdigest()[:8]
    CUSTOM_PALETTES[name] = roles
    return name, roles
