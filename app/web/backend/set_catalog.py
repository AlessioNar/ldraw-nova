"""Read-only access to the toolkit's offline set catalogue (ldraw_tools/data/set-catalog.bin) from the backend.

The backend's system Python cannot import ldraw_tools (see inventories.py), and a subprocess per search keystroke
would be far too slow, so this module reads the file directly. The layout is documented in ldraw_tools/sets.py:

    b"LDSETCAT1\n" | u32 header length | zlib(JSON header) | per-set blobs (zlib of varint streams)

tests/test_set_catalog_reader.py in the toolkit checks this reader against ldraw_tools.sets on a real catalogue.
"""
from __future__ import annotations

import os
import struct
import zlib
import json
from functools import lru_cache
from pathlib import Path


MAGIC = b"LDSETCAT1\n"


class CatalogError(ValueError):
    """The catalogue is missing or unreadable."""


def catalog_path() -> Path:
    import settings                  # imported here so the reader itself needs nothing but the standard library
    override = os.environ.get("LDRAW_NOVA_SET_CATALOG")
    return Path(override) if override else settings.TOOLKIT_DIR / "ldraw_tools" / "data" / "set-catalog.bin"


def _varints(data, count, pos):
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


class Catalog:
    def __init__(self, data: bytes):
        if not data.startswith(MAGIC):
            raise CatalogError("not a set catalogue (bad magic)")
        (size,) = struct.unpack_from("<I", data, len(MAGIC))
        start = len(MAGIC) + 4
        try:
            header = json.loads(zlib.decompress(data[start:start + size]))
        except (zlib.error, ValueError) as exc:
            raise CatalogError(f"set catalogue header is unreadable: {exc}") from exc
        self._data = memoryview(data)[start + size:]
        self.meta = header["meta"]
        self.parts = header["parts"]
        self.sets: list[dict] = []
        self._offsets: dict[str, tuple[int, int]] = {}
        for num, name, year, theme, num_parts, version, mapped, dpart, dcolour, offset, length in header["sets"]:
            self.sets.append(dict(set_num=num, name=name, year=year, theme=header["themes"][theme], num_parts=num_parts,
                                  inv_version=version, mapped_pieces=mapped, dropped_part=dpart, dropped_colour=dcolour))
            self._offsets[num] = (offset, length)
        self.by_num = {s["set_num"]: s for s in self.sets}
        self._haystack = [f"{s['set_num']} {s['name']} {s['theme']}".casefold() for s in self.sets]

    def lines(self, num: str) -> list[tuple[str, int, int]]:
        offset, length = self._offsets[num]
        raw = zlib.decompress(self._data[offset:offset + length])
        (n,), pos = _varints(raw, 1, 0)
        deltas, pos = _varints(raw, n, pos)
        colours, pos = _varints(raw, n, pos)
        qtys, pos = _varints(raw, n, pos)
        out, idx = [], 0
        for d, c, q in zip(deltas, colours, qtys):
            idx += d
            out.append((self.parts[idx], c, q))
        return out

    def get(self, set_num: str) -> dict | None:
        set_num = set_num.strip()
        if set_num not in self.by_num and "-" not in set_num:
            set_num += "-1"
        return self.by_num.get(set_num)

    def search(self, query: str = "", limit: int = 20) -> list[dict]:
        """Sets whose number, name or theme contain every word; an exact number first, then newest."""
        words = query.casefold().split()
        exact = self.get(query) if query.strip() else None
        hits = [s for s, hay in zip(self.sets, self._haystack) if all(w in hay for w in words)]
        hits.sort(key=lambda s: (s is not exact, -s["year"], s["set_num"]))
        return hits[:limit]


def popularity(catalog: "Catalog") -> dict[str, int]:
    """{part: number of catalogue sets that contain it}: "what real builders use", to order equally good readings."""
    found = getattr(catalog, "_popularity", None)
    if found is None:
        counts: dict[str, int] = {}
        parts = catalog.parts
        for num in catalog._offsets:
            offset, length = catalog._offsets[num]
            raw = zlib.decompress(catalog._data[offset:offset + length])
            (n,), pos = _varints(raw, 1, 0)
            deltas, _ = _varints(raw, n, pos)
            idx = 0
            for d in deltas:
                idx += d
                counts[parts[idx]] = counts.get(parts[idx], 0) + 1
        found = catalog._popularity = counts
    return found


@lru_cache(maxsize=2)
def _load(path: str, mtime: int) -> Catalog:
    try:
        return Catalog(Path(path).read_bytes())
    except OSError as exc:
        raise CatalogError(f"set catalogue {path} is unavailable: {exc}") from exc


def load() -> Catalog:
    path = catalog_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as exc:
        raise CatalogError(f"set catalogue {path} is unavailable: {exc}") from exc
    return _load(str(path), mtime)
