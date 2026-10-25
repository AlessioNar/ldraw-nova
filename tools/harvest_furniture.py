#!/usr/bin/env python3
"""Derive the furniture catalogue from the reference sets.

    python tools/harvest_furniture.py [--models data/models-annotated] [--out ldraw_tools/data/furniture-catalogue.json]

Every submodel whose name says it is a piece of furniture (chair, table, bed, sofa, shelf...) is flattened to its parts. Per
kind the catalogue records how many pieces and sets were found, the parts they use most (the vocabulary the furniture
generator draws on), the typical footprint and height, and which sets the pieces came from. Footprints come from the part
dimension table with each part's box rotated by its matrix; they are measured to within about a stud, which is enough to
size a piece for a room. The output is deterministic, so it is committed and a test keeps the generator inside it.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'data'))
from categories.dimensions import get_dimensions  # noqa: E402

# kind -> (name pattern on the lower-cased submodel name, family of use). Order matters: the first match wins.
KINDS = {
    'armchair': r'arm-?chair',
    'sofa': r'sofa|couch|settee',
    'chair': r'(?<![a-z])chair|deckchair',
    'stool': r'stool',
    'bed': r'(?<![a-z])(?:baby|single|double|bunk)?bed(?:single|double)?\d?$|^bed|(?<![a-z])bed(?![a-z])',
    'desk': r'(?<![a-z])desk|writing table',
    'table': r'(?<![a-z])(?:round|dining|coffee|dressing|side|kitchen)?table(?:-stool)?\d?$|(?<![a-z])table(?![a-z])',
    'bench': r'(?<![a-z])bench',
    'shelf': r'shelf|shelves|bookcase|bookshelf',
    'cabinet': r'cabinet|wardrobe|cupboard|dresser|drawers',
}
EXCLUDE = re.compile(r'turntable|constable|workbench|inflatable|ribbedhose|bedroom|stable|sunbed')
LINE1 = re.compile(r'^1\s+(-?\d+)\s+((?:-?[\d.]+\s+){12})(\S+)')


def blocks(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    name = None
    for raw in path.read_text(errors='replace').splitlines():
        line = raw.strip()
        if line.startswith('0 FILE '):
            name = line[7:].strip().lower()
            out[name] = []
        elif name is not None and line.startswith('1 '):
            out[name].append(line)
    return out


def kind_of(name: str) -> str | None:
    base = re.sub(r'\.(ldr|mpd|dat)$', '', name.split('/')[-1])
    base = re.sub(r'^\d+-\d+\s*-\s*', '', base)         # "10243-1 - Chair" -> "chair"
    base = base.split(' - ')[-1].strip()
    if EXCLUDE.search(base):
        return None
    for kind, pattern in KINDS.items():
        if re.search(pattern, base):
            return kind
    return None


def flatten(blks, name, matrix=(1, 0, 0, 0, 1, 0, 0, 0, 1), origin=(0.0, 0.0, 0.0), depth=0):
    """Parts of a submodel as (part id, colour, matrix, position) in the piece's own frame."""
    if depth > 6:
        return
    for line in blks[name]:
        m = LINE1.match(line)
        if not m:
            continue
        colour, nums, ref = int(m.group(1)), [float(v) for v in m.group(2).split()], m.group(3).lower()
        pos, mat = nums[:3], nums[3:]
        world = tuple(origin[i] + sum(matrix[3 * i + j] * pos[j] for j in range(3)) for i in range(3))
        combined = tuple(sum(matrix[3 * i + k] * mat[3 * k + j] for k in range(3)) for i in range(3) for j in range(3))
        if ref in blks:
            yield from flatten(blks, ref, combined, world, depth + 1)
        else:
            yield re.sub(r'\.dat$', '', ref), colour, combined, world


def measure(parts):
    """(studs x, studs z, height in plates) of the union of part boxes; None when no part has a known size."""
    lo, hi = [1e9] * 3, [-1e9] * 3
    for pid, _, mat, pos in parts:
        d = get_dimensions(pid)
        if not d:
            continue
        size = (d['ldu_x'], d['ldu_y'], d['ldu_z'])
        # LDraw parts are centred on x and z and hang down (+y) from their origin: the box centre is half a height below it.
        local = (0, size[1] / 2, 0)
        for axis in range(3):
            centre = pos[axis] + sum(mat[3 * axis + j] * local[j] for j in range(3))
            half = sum(abs(mat[3 * axis + j]) * size[j] / 2 for j in range(3))
            lo[axis], hi[axis] = min(lo[axis], centre - half), max(hi[axis], centre + half)
    if lo[0] > hi[0]:
        return None
    return round((hi[0] - lo[0]) / 20), round((hi[2] - lo[2]) / 20), round((hi[1] - lo[1]) / 8)


def harvest(models: Path) -> dict:
    pieces = defaultdict(list)
    for path in sorted(models.glob('*.mpd')):
        set_id = path.name.split('_')[0].replace('.mpd', '')
        blks = blocks(path)
        for name in blks:
            kind = kind_of(name)
            if not kind or not blks[name]:
                continue
            parts = list(flatten(blks, name))
            if 2 <= len(parts) <= 60:
                pieces[kind].append((set_id, name, parts))
    catalogue = {}
    for kind in KINDS:
        rows = pieces.get(kind, [])
        if not rows:
            continue
        # one vote per (set, piece): the same piece repeated in a model's submodels counts once
        unique = {(s, tuple(sorted(Counter(p[0] for p in parts).items()))): (s, n, parts) for s, n, parts in rows}
        rows = sorted(unique.values(), key=lambda r: (r[0], r[1]))
        use, sets = Counter(), set()
        sizes = []
        for set_id, _, parts in rows:
            sets.add(set_id)
            use.update(set(p[0] for p in parts))
            size = measure(parts)
            if size:
                sizes.append(size)
        median = [int(statistics.median(s[i] for s in sizes)) if sizes else None for i in range(3)]
        catalogue[kind] = {
            'pieces': len(rows), 'sets': len(sets),
            'footprint_studs': median[:2], 'height_plates': median[2],
            'parts': [{'part': p, 'pieces': n} for p, n in sorted(use.items(), key=lambda kv: (-kv[1], kv[0]))[:24]],
            'example_sets': sorted(sets)[:8],
        }
    return catalogue


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--models', default=str(ROOT / 'data/models-annotated'))
    ap.add_argument('--out', default=str(ROOT / 'ldraw_tools/data/furniture-catalogue.json'))
    args = ap.parse_args(argv)
    doc = {'description': 'Furniture found in the reference sets: counts, typical size and the parts used most. Regenerate with tools/harvest_furniture.py.',
           'kinds': harvest(Path(args.models))}
    Path(args.out).write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    for kind, row in doc['kinds'].items():
        print(f"{kind:9} {row['pieces']:4} pieces in {row['sets']:3} sets  {row['footprint_studs']} studs x {row['height_plates']} plates  "
              + ' '.join(p['part'] for p in row['parts'][:8]))


if __name__ == '__main__':
    main()
