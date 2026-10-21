"""Catalog of real LEGO sets: parts lists only, no geometry (data/set-catalog.bin).

Built by tools/build_set_catalog.py from Rebrickable's database downloads (see ATTRIBUTION.md and
docs/plans/set-catalog.md). Per set: Rebrickable name, year, theme path, piece count and the physical parts
list as (LDraw part, LDraw colour, qty): spares excluded, minifigs and sub-sets expanded, pieces without an
LDraw equivalent counted in ``dropped_part`` / ``dropped_colour`` instead of listed.

File layout (all integers little endian):

    b"LDSETCAT1\\n" | u32 header length | zlib(JSON header) | per-set blobs

The JSON header holds ``meta``, the sorted ``parts`` table, the ``themes`` table and one row per set
(``ROW_FIELDS`` plus the byte offset and length of the set's blob, relative to the end of the header). A blob
is zlib(varints): the line count n, then n part-index deltas, n colour codes, n quantities (lines sorted by
part index, colour). Random access by set number reads one blob; the whole file (a few MB) is held in memory.
"""
from __future__ import annotations

import json
import os
import re
import struct
import tarfile
import zipfile
import zlib
from functools import lru_cache
from pathlib import Path

from .common import DATA, models_path

CATALOG_FILE = DATA / "set-catalog.bin"
MAGIC = b"LDSETCAT1\n"
ROW_FIELDS = ("set_num", "name", "year", "theme", "num_parts", "inv_version", "mapped_pieces", "dropped_part", "dropped_colour", "lines")


class CatalogError(ValueError):
    """The set catalog is missing or unreadable."""


# ------------------------------------------------------------------- encoding

def _put_varint(out, n):
    while n >= 0x80:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)


def _get_varints(data, count, pos):
    values = []
    for _ in range(count):
        shift = result = 0
        while True:
            b = data[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if b < 0x80:
                break
            shift += 7
        values.append(result)
    return values, pos


def encode_lines(lines, part_index):
    """Blob for [(part, colour, qty)] (any order); duplicate lines are not allowed."""
    ordered = sorted((part_index[p], c, q) for p, c, q in lines)
    out = bytearray()
    _put_varint(out, len(ordered))
    prev = 0
    for i, _, _ in ordered:
        _put_varint(out, i - prev)
        prev = i
    for _, c, _ in ordered:
        _put_varint(out, c)
    for _, _, q in ordered:
        _put_varint(out, q)
    return zlib.compress(bytes(out), 9)


def write_catalog(path, meta, sets):
    """Write the catalog atomically; ``sets`` are dicts with ROW_FIELDS keys (lines: [(part, colour, qty)]). Returns the size."""
    path = Path(path)
    parts = sorted({p for s in sets for p, _, _ in s["lines"]})
    part_index = {p: i for i, p in enumerate(parts)}
    themes = sorted({s["theme"] for s in sets})
    theme_index = {t: i for i, t in enumerate(themes)}
    rows, blobs, offset = [], [], 0
    for s in sorted(sets, key=lambda s: s["set_num"]):
        blob = encode_lines(s["lines"], part_index)
        mapped = sum(q for _, _, q in s["lines"])
        rows.append([s["set_num"], s["name"], s["year"], theme_index[s["theme"]], s["num_parts"], s["inv_version"], mapped,
                     s["dropped_part"], s["dropped_colour"], offset, len(blob)])
        blobs.append(blob)
        offset += len(blob)
    header = zlib.compress(json.dumps(dict(meta=meta, parts=parts, themes=themes, sets=rows), sort_keys=True,
                                      separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 9)
    data = MAGIC + struct.pack("<I", len(header)) + header + b"".join(blobs)
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return len(data)


# ------------------------------------------------------------------- reading

class Catalog:
    """Lazily decoded set catalog: header in memory, per-set parts lists decoded on demand."""

    def __init__(self, data):
        if not data.startswith(MAGIC):
            raise CatalogError("not a set catalog (bad magic)")
        (size,) = struct.unpack_from("<I", data, len(MAGIC))
        start = len(MAGIC) + 4
        try:
            header = json.loads(zlib.decompress(data[start:start + size]))
        except (zlib.error, ValueError) as exc:
            raise CatalogError(f"set catalog header is unreadable: {exc}") from exc
        self._data = memoryview(data)[start + size:]
        self.meta = header["meta"]
        self.parts = header["parts"]
        self.sets = []
        self._offsets = {}
        for num, name, year, theme, num_parts, version, mapped, dpart, dcolour, offset, length in header["sets"]:
            self.sets.append(dict(set_num=num, name=name, year=year, theme=header["themes"][theme], num_parts=num_parts,
                                  inv_version=version, mapped_pieces=mapped, dropped_part=dpart, dropped_colour=dcolour))
            self._offsets[num] = (offset, length)
        self.by_num = {s["set_num"]: s for s in self.sets}
        self._index = None
        self._nlines = None

    def __len__(self):
        return len(self.sets)

    def _decode(self, num):
        offset, length = self._offsets[num]
        raw = zlib.decompress(self._data[offset:offset + length])
        n, pos = _get_varints(raw, 1, 0)
        n = n[0]
        deltas, pos = _get_varints(raw, n, pos)
        colours, pos = _get_varints(raw, n, pos)
        qtys, pos = _get_varints(raw, n, pos)
        return deltas, colours, qtys

    def lines(self, num):
        """[(part, colour, qty)] of one set, sorted by part index then colour."""
        deltas, colours, qtys = self._decode(num)
        out, idx = [], 0
        for d, c, q in zip(deltas, colours, qtys):
            idx += d
            out.append((self.parts[idx], c, q))
        return out

    def index(self):
        """Inverted index {(part, colour): ([set index], [qty])} over every set, built once per process."""
        if self._index is None:
            parts, index, nlines = self.parts, {}, []
            for si, s in enumerate(self.sets):
                deltas, colours, qtys = self._decode(s["set_num"])
                nlines.append(len(deltas))
                idx = 0
                for d, c, q in zip(deltas, colours, qtys):
                    idx += d
                    key = (parts[idx], c)
                    entry = index.get(key)
                    if entry is None:
                        index[key] = ([si], [q])
                    else:
                        entry[0].append(si)
                        entry[1].append(q)
            self._index, self._nlines = index, nlines
        return self._index

    def line_counts(self):
        """Distinct (part, colour) lines per set, in ``sets`` order."""
        self.index()
        return self._nlines


def read_catalog(path):
    try:
        return Catalog(Path(path).read_bytes())
    except OSError as exc:
        raise CatalogError(f"set catalog {path} is unavailable: {exc}") from exc


@lru_cache(maxsize=2)
def _load(path, mtime):
    return read_catalog(path)


def load_catalog(path=None):
    """Process-wide cached catalog (``path`` defaults to $LDRAW_NOVA_SET_CATALOG, then data/set-catalog.bin)."""
    path = Path(path or os.environ.get("LDRAW_NOVA_SET_CATALOG") or CATALOG_FILE)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as exc:
        raise CatalogError(f"set catalog {path} is unavailable: {exc}") from exc
    return _load(str(path), mtime)


# ------------------------------------------------------------------- queries

def get_set(set_num, catalog=None):
    """Metadata dict of a set (``8448`` is taken as ``8448-1``), or None."""
    catalog = catalog or load_catalog()
    set_num = set_num.strip()
    if set_num not in catalog.by_num and "-" not in set_num:
        set_num += "-1"
    return catalog.by_num.get(set_num)


def set_bom(set_num, catalog=None):
    """Physical parts list {(part, colour): qty} of a set; KeyError when the set is not in the catalog."""
    catalog = catalog or load_catalog()
    found = get_set(set_num, catalog)
    if found is None:
        raise KeyError(set_num)
    return {(p, c): q for p, c, q in catalog.lines(found["set_num"])}


def search_sets(query="", *, theme=None, year=None, min_parts=None, max_parts=None, limit=50, catalog=None):
    """Sets whose set number, name or theme contain every word of ``query`` (case-insensitive), narrowed by
    theme substring, year (an int, or a (first, last) pair) and piece count; newest first."""
    catalog = catalog or load_catalog()
    words = query.casefold().split()
    theme = theme.casefold() if theme else None
    out = []
    for s in catalog.sets:
        if theme and theme not in s["theme"].casefold():
            continue
        if year is not None and not (s["year"] == year if isinstance(year, int) else year[0] <= s["year"] <= year[1]):
            continue
        if min_parts is not None and s["num_parts"] < min_parts:
            continue
        if max_parts is not None and s["num_parts"] > max_parts:
            continue
        if words:
            haystack = f"{s['set_num']} {s['name']} {s['theme']}".casefold()
            if not all(w in haystack for w in words):
                continue
        out.append(s)
    out.sort(key=lambda s: (-s["year"], s["set_num"]))
    return out[:limit] if limit else out


def catalog_stats(catalog=None):
    catalog = catalog or load_catalog()
    mapped = sum(s["mapped_pieces"] for s in catalog.sets)
    dropped_part = sum(s["dropped_part"] for s in catalog.sets)
    dropped_colour = sum(s["dropped_colour"] for s in catalog.sets)
    total = mapped + dropped_part + dropped_colour
    years = [s["year"] for s in catalog.sets if s["year"]]
    themes = {}
    for s in catalog.sets:
        themes[s["theme"].split(" > ")[0]] = themes.get(s["theme"].split(" > ")[0], 0) + 1
    return dict(sets=len(catalog.sets), distinct_parts=len(catalog.parts), pieces=total, mapped_pieces=mapped,
                dropped_part_pieces=dropped_part, dropped_colour_pieces=dropped_colour,
                mapped_share=mapped / total if total else 0.0, first_year=min(years, default=0), last_year=max(years, default=0),
                root_themes=dict(sorted(themes.items(), key=lambda kv: (-kv[1], kv[0]))), meta=catalog.meta)


# ------------------------------------------------------------ bundled models

_SET_PREFIX = re.compile(r"^([^_.]+)")


def _model_names(root):
    root = Path(root)
    if root.is_dir():
        return [p.name for p in root.iterdir()]
    for suffix in (".zip", ".tar.gz", ".tgz", ".tar.xz", ".tar"):  # the folder may ship only as an archive
        archive = root.with_name(root.name + suffix)
        if archive.is_file():
            if suffix == ".zip":
                with zipfile.ZipFile(archive) as z:
                    return [Path(n).name for n in z.namelist()]
            with tarfile.open(archive) as t:
                return [Path(n).name for n in t.getnames()]
    return []


@lru_cache(maxsize=4)
def _bundled(root):
    try:
        names = _model_names(root)
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return frozenset()
    return frozenset(m.group(1) for n in names if n.lower().endswith((".mpd", ".ldr")) and (m := _SET_PREFIX.match(n)))


def bundled_set_nums(root=None):
    """Set numbers that also exist as a bundled LDraw model (file name prefix before ``_`` or ``.``); empty when unknown."""
    return _bundled(str(models_path(root)))


# ------------------------------------------------------------------- ranking

def _result(s, ok, full_lines, distinct_lines, bundled):
    mapped = s["mapped_pieces"]
    dropped = s["dropped_part"] + s["dropped_colour"]
    return dict(set_num=s["set_num"], name=s["name"], year=s["year"], theme=s["theme"], num_parts=s["num_parts"],
                needed=mapped, owned=ok, missing=mapped - ok, completeness=ok / mapped, lines=distinct_lines,
                lines_complete=full_lines, dropped_pieces=dropped, mapped_share=mapped / (mapped + dropped),
                has_model=s["set_num"] in bundled)


def rank_sets(source, inventory_counts, *, limit=20, min_parts=None, max_parts=None, max_missing_share=None, theme=None,
              query="", min_completeness=0.0, bundled=None):
    """Rank sets by how much of their parts list the inventory covers.

    ``inventory_counts`` is {(part, colour): owned qty} (inventory.combine). ``source`` is a Catalog (inverted
    index, only owned lines are visited) or an iterable of (info dict, {(part, colour): qty}) pairs, ``info``
    carrying set_num, name, year, theme, num_parts and optionally dropped_part/dropped_colour.
    completeness = sum(min(needed, owned)) / sum(needed), the measure inventory.check_model reports, over
    the mapped parts list; pieces without an LDraw equivalent are in dropped_pieces / mapped_share.
    Sets without a mappable piece are skipped. ``bundled`` is the set of set numbers that have a model.
    """
    bundled = bundled_set_nums() if bundled is None else bundled
    words = query.casefold().split()
    theme = theme.casefold() if theme else None

    def wanted(s):
        if s["mapped_pieces"] <= 0:
            return False
        if min_parts is not None and s["num_parts"] < min_parts:
            return False
        if max_parts is not None and s["num_parts"] > max_parts:
            return False
        if theme and theme not in s["theme"].casefold():
            return False
        if words and not all(w in f"{s['set_num']} {s['name']} {s['theme']}".casefold() for w in words):
            return False
        return True

    results = []
    if isinstance(source, Catalog):
        sets_ = source.sets
        ok, full, size = [0] * len(sets_), [0] * len(sets_), source.line_counts()
        for key, have in inventory_counts.items():
            entry = source.index().get(key)
            if entry is None or have <= 0:
                continue
            for si, q in zip(*entry):
                if q <= have:
                    ok[si] += q
                    full[si] += 1
                else:
                    ok[si] += have
        for si, s in enumerate(sets_):
            if ok[si] and wanted(s):
                results.append(_result(s, ok[si], full[si], size[si], bundled))
    else:
        for info, bom in source:
            s = dict(info, mapped_pieces=sum(bom.values()), dropped_part=info.get("dropped_part", 0), dropped_colour=info.get("dropped_colour", 0))
            if not wanted(s):
                continue
            got = sum(min(q, inventory_counts.get(k, 0)) for k, q in bom.items())
            if got:
                results.append(_result(s, got, sum(1 for k, q in bom.items() if inventory_counts.get(k, 0) >= q), len(bom), bundled))
    if max_missing_share is not None:
        results = [r for r in results if r["missing"] <= max_missing_share * r["needed"]]
    results = [r for r in results if r["completeness"] >= min_completeness]
    results.sort(key=lambda r: (-r["completeness"], -r["needed"], r["set_num"]))
    return results[:limit] if limit else results
