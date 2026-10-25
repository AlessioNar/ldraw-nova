"""Read-only access to the toolkit's offline set catalogue (ldraw_tools/data/set-catalog.bin) from the backend.

The backend's system Python cannot import ldraw_tools (see inventories.py), and a subprocess per search keystroke
would be far too slow, so this module reads the file directly. The layout is documented in ldraw_tools/sets.py:

    b"LDSETCAT1\n" | u32 header length | zlib(JSON header) | per-set blobs (zlib of varint streams)

tests/test_set_catalog_reader.py in the toolkit checks this reader against ldraw_tools.sets on a real catalogue.

An optional local catalogue (data/local/set-catalog-local.json, written by tools/fetch_cada_sets.py, never committed) is merged
in at load time. Its entries are other brands' sets, keyed "<brand>:<number>" (cada:C51026W) so numbers cannot clash across
brands, and carry no parts list: the parts come in later through an import. A missing or broken local file changes nothing.
"""
from __future__ import annotations

import os
import struct
import zlib
import json
from functools import lru_cache
from pathlib import Path


MAGIC = b"LDSETCAT1\n"
LOCAL_FORMAT = "set-catalog-local/1"
BRAND_NAMES = {"lego": "LEGO", "cada": "CaDA"}


class CatalogError(ValueError):
    """The catalogue is missing or unreadable."""


def catalog_path() -> Path:
    import settings                  # imported here so the reader itself needs nothing but the standard library
    override = os.environ.get("LDRAW_NOVA_SET_CATALOG")
    return Path(override) if override else settings.TOOLKIT_DIR / "ldraw_tools" / "data" / "set-catalog.bin"


def local_catalog_path() -> Path:
    import settings
    override = os.environ.get("LDRAW_NOVA_LOCAL_SET_CATALOG")
    return Path(override) if override else settings.TOOLKIT_DIR / "data" / "local" / "set-catalog-local.json"


def _local_sets(path: Path) -> list[dict]:
    """Set records of the local catalogue file; [] when it is absent, unreadable or in another format."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(doc, dict) or doc.get("format") != LOCAL_FORMAT or not isinstance(doc.get("sets"), list):
        return []
    found = []
    for entry in doc["sets"]:
        if not isinstance(entry, dict):
            continue
        brand_id, number, name = (str(entry.get(k) or "").strip() for k in ("brand", "set_num", "name"))
        if not brand_id or not number or not name:
            continue
        year, pieces = entry.get("year"), entry.get("pieces")
        found.append(dict(set_num=f"{brand_id.casefold()}:{number}", number=number, brand=BRAND_NAMES.get(brand_id.casefold(), brand_id),
                          name=name, year=year if isinstance(year, int) else 0, theme="",
                          num_parts=pieces if isinstance(pieces, int) else 0, inv_version=0, mapped_pieces=0, dropped_part=0, dropped_colour=0))
    return found


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
    def __init__(self, data: bytes, local: list[dict] | None = None):
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
                                  inv_version=version, mapped_pieces=mapped, dropped_part=dpart, dropped_colour=dcolour,
                                  brand="LEGO", number=num))
            self._offsets[num] = (offset, length)
        self.by_num = {s["set_num"]: s for s in self.sets}
        self.by_number: dict[str, dict] = {}                          # other brands' sets by their bare number, case-insensitive
        for s in local or []:
            if s["set_num"] not in self.by_num:
                self.sets.append(s)
                self.by_num[s["set_num"]] = s
                self.by_number.setdefault(s["number"].casefold(), s)
        self._haystack = [f"{s['set_num']} {s['number']} {s['brand']} {s['name']} {s['theme']}".casefold() for s in self.sets]

    def lines(self, num: str) -> list[tuple[str, int, int]]:
        if num not in self._offsets:                                  # a local-catalogue set: no parts list
            return []
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
        if set_num in self.by_num:
            return self.by_num[set_num]
        local = self.by_number.get(set_num.casefold()) or self.by_num.get(set_num.casefold())
        if local is not None:
            return local
        if "-" not in set_num:
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
def _load(path: str, mtime: int, local_path: str, local_mtime: int) -> Catalog:
    try:
        return Catalog(Path(path).read_bytes(), _local_sets(Path(local_path)))
    except OSError as exc:
        raise CatalogError(f"set catalogue {path} is unavailable: {exc}") from exc


def load() -> Catalog:
    path = catalog_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as exc:
        raise CatalogError(f"set catalogue {path} is unavailable: {exc}") from exc
    local = local_catalog_path()
    try:
        local_mtime = local.stat().st_mtime_ns
    except OSError:
        local_mtime = 0
    return _load(str(path), mtime, str(local), local_mtime)
