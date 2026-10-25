"""What the backend knows about LDraw parts and colours for the collection: description, category, size, colour names.

One lookup point (cached) so the collection browser, quick add and the thumbnails agree, and so tests can replace it
with a small table. Everything comes from the baked LDraw library (`ldraw.parts_index()`, LDConfig colours).
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

log = logging.getLogger(__name__)

HIDDEN_COLOURS = {16, 24}            # "main colour" and "edge colour" are not colours a person owns
NUM = r"\d+(?:\.\d+)?"
SIZE_RE = re.compile(rf"(?<![\w.])({NUM})\s*x\s*({NUM})(?:\s*x\s*({NUM}))?(?![\w.])", re.IGNORECASE)


@lru_cache(maxsize=1)
def meta() -> dict[str, tuple[str, str]]:
    """{part id without .dat: (description, category)}; empty when the library is unavailable."""
    try:
        import ldraw
        return {pid.removesuffix(".dat").casefold(): (" ".join(description.split()), category)
                for pid, description, category, *_ in ldraw.parts_index()}
    except Exception as exc:            # no library or index: names are optional
        log.warning("part descriptions unavailable: %s", exc)
        return {}


@lru_cache(maxsize=1)
def colour_names() -> dict[int, str]:
    """{LDraw colour code: name with spaces}, without the two pseudo colours."""
    try:
        import ldraw
        return {code: name.replace("_", " ") for code, name in ldraw.colours().items() if code not in HIDDEN_COLOURS}
    except Exception as exc:
        log.warning("colour names unavailable: %s", exc)
        return {}


def clear_caches() -> None:
    meta.cache_clear()
    colour_names.cache_clear()


def describe(part: str) -> str:
    return meta().get(part.casefold(), ("", ""))[0]


def category(part: str) -> str:
    return meta().get(part.casefold(), ("", ""))[1]


def size_from_match(m: re.Match) -> str:
    """The dimensions of a SIZE_RE match, smallest first so 4x2 and 2x4 agree: "2x4", "1x2x1.333"."""
    return "x".join(sorted((g for g in m.groups() if g), key=float))


def size_of(description: str) -> str:
    """"Brick 2 x 4" -> "2x4"; "" when the description has no size."""
    m = SIZE_RE.search(description)
    return size_from_match(m) if m else ""


def colour_name(code: int) -> str:
    return colour_names().get(code, f"Colour {code}")
