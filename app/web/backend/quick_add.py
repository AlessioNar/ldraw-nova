"""Quick add, rules only: understand lines such as "20 red 2x4 bricks", "3 x 3001 blue", "10 plate 1x2 dark bluish grey".

A small grammar (quantity, colour words, a size like 2x4 or 2 x 4, shape words such as brick/plate/tile/round, or a part
number) resolved against the LDraw part index and colour table. There is no model call and no silent guess: every line
comes back as what was understood plus a status:

    ok            part, colour and quantity are all known
    needs_part    the part is unknown or ambiguous (candidates are listed when there are any)
    needs_colour  the part is known but no colour was given, or the colour word is ambiguous (plain "grey")

Typos are corrected only toward known colour and shape words, and every correction is reported in `notes`.
Italian colour and shape words work through a small synonym table.
"""
from __future__ import annotations

import difflib
import re
import threading

import part_info

MAX_LINES = 60
MAX_CANDIDATES = 8
MAX_QTY = 100_000
MAX_TEXT = 4000

STOP = {"x", "of", "pcs", "pc", "piece", "pieces", "the", "a", "an", "di", "pezzi", "pezzo", "n", "no"}
# Italian (and a few spelling variants) -> the English word the part descriptions and LDraw colour names use
SYNONYMS = {
    "rosso": "red", "rossa": "red", "blu": "blue", "giallo": "yellow", "gialla": "yellow", "verde": "green", "nero": "black",
    "nera": "black", "bianco": "white", "bianca": "white", "grigio": "gray", "grigia": "gray", "arancione": "orange",
    "arancio": "orange", "marrone": "brown", "rosa": "pink", "viola": "purple", "beige": "tan", "scuro": "dark", "scura": "dark",
    "chiaro": "light", "chiara": "light", "trasparente": "trans", "trasparenti": "trans", "grey": "gray", "colour": "", "color": "",
    "mattoncino": "brick", "mattoncini": "brick", "mattone": "brick", "mattoni": "brick", "piastra": "plate", "piastre": "plate",
    "tessera": "tile", "tessere": "tile", "piastrella": "tile", "piastrelle": "tile", "rotondo": "round", "rotonda": "round",
    "tondo": "round", "tonda": "round", "ruota": "wheel", "ruote": "wheel", "pendenza": "slope", "tyres": "tyre", "tires": "tyre",
    "tire": "tyre",
}
SHAPES = ("brick", "plate", "tile", "slope", "round", "wheel", "tyre", "axle", "pin", "beam", "liftarm", "panel", "window",
          "door", "hinge", "arch", "corner", "wedge", "cylinder", "cone", "dish", "bar", "antenna", "flag", "plant", "technic",
          "baseplate", "bracket", "connector", "gear", "chain", "hose", "windscreen", "steering", "jumper", "modified", "inverted")
# shape words that name a family of parts: with one of these a unique best description is a safe reading
CATEGORY_WORDS = {"brick", "plate", "tile", "slope", "wheel", "tyre", "axle", "pin", "beam", "liftarm", "panel", "window", "door",
                  "hinge", "arch", "wedge", "cylinder", "cone", "dish", "bar", "baseplate", "bracket", "gear", "windscreen"}
# plain "grey" is genuinely ambiguous between four LDraw colours: list them, never pick one
AMBIGUOUS_COLOURS = {"gray": [71, 72, 7, 8], "bluish gray": [71, 72]}
COLOUR_ALIASES = {"lbg": "light bluish gray", "dbg": "dark bluish gray", "clear": "trans clear", "transparent": "trans clear",
                  "lime green": "lime", "gold": "pearl gold", "silver": "flat silver"}
MODIFIERS = {"dark", "light"}
NOT_A_PART_PREFIX = ("~", "=", "_", "|")
PRINTED = re.compile(r"(?:pr|pb|px|ps|pt|pat|bp)\d")
SKIP_CATEGORIES = {"Moved", "Obsolete", "Sticker", "Sticker Shortcut"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zà-ú0-9]+", text.casefold().replace("×", "x"))


def _norm_colour(name: str) -> str:
    return " ".join(_words(name.replace("_", " "))).replace("grey", "gray")


def colour_table() -> dict[str, int]:
    """{normalised colour phrase: LDraw code}: the LDraw names (grey spelt gray) plus a few aliases."""
    table: dict[str, int] = {}
    for code, name in sorted(part_info.colour_names().items()):
        table.setdefault(_norm_colour(name), code)
    for alias, target in COLOUR_ALIASES.items():
        if target in table:
            table.setdefault(alias, table[target])
    return table


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"[\n;,+&]|\band\b|\bplus\b", text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _description_words(description: str) -> list[str]:
    return [w for w in _words(description) if not w.isdigit() and w != "x"]


def _singular(word: str, vocab) -> str:
    """"bricks" -> "brick" when the singular is a word the descriptions use (even if the plural is too: "Side Bricks")."""
    if len(word) < 4:
        return word
    for stem in (word[:-1] if word.endswith("s") else "", word[:-2] if word.endswith("es") else ""):
        if stem and stem in vocab:
            return stem
    return word


class _Index:
    """Searchable rows (plain, current, unprinted parts) and the words used in their descriptions."""

    def __init__(self, meta: dict[str, tuple[str, str]]):
        self.meta = meta
        self.vocab: set[str] = set()
        self.rows: list[tuple[str, str, str, frozenset, str]] = []      # pid, description, category, words, size
        for pid, (description, category) in meta.items():
            if description.startswith(NOT_A_PART_PREFIX) or category in SKIP_CATEGORIES or PRINTED.search(pid):
                continue
            words = _description_words(description)
            self.vocab.update(words)
            self.rows.append((pid, description, category, frozenset(words), part_info.size_of(description)))


_index_cache: dict[int, _Index] = {}


def _index() -> _Index:
    meta = part_info.meta()
    found = _index_cache.get(id(meta))
    if found is None or found.meta is not meta:
        _index_cache.clear()
        found = _index_cache[id(meta)] = _Index(meta)
    return found


def _fuzzy(word: str, vocabulary) -> str | None:
    if len(word) < 3 or word.isdigit():
        return None
    close = difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.8 if len(word) == 3 else 0.75)
    return close[0] if close else None


def _find_colour(tokens: list[str], table: dict[str, int]):
    """(first token index, token count, code or None, candidate codes) of the longest colour phrase, or None."""
    names = {**{k: [v] for k, v in table.items()}, **{k: v for k, v in AMBIGUOUS_COLOURS.items() if k not in table}}
    for size in range(min(4, len(tokens)), 0, -1):
        for start in range(len(tokens) - size + 1):
            phrase = " ".join(tokens[start:start + size])
            if phrase in names:
                codes = names[phrase]
                return start, size, (codes[0] if len(codes) == 1 else None), codes
    return None


def _rank(rows, required: set, size: str):
    scored = []
    for pid, description, category, words, row_size in rows:
        if (size and row_size != size) or not required <= words:
            continue
        scored.append(((len(words - required), 0 if pid.isdigit() else 1, len(pid), pid), pid, description, category))
    scored.sort()
    return scored


def _stem(pid: str) -> str:
    m = re.match(r"\d+", pid)
    return m.group() if m else pid


_popular: dict[str, int] | None = None


def popular() -> dict[str, int]:
    """How many catalogue sets use each part (empty without the catalogue); only orders candidates, never decides."""
    global _popular
    if _popular is None:
        try:
            import set_catalog
            _popular = set_catalog.popularity(set_catalog.load())
        except Exception:
            _popular = {}
    return _popular


_warming = False


def warm() -> None:
    """Computes the part popularity in the background (a few seconds once) so the first ambiguous line is not slow."""
    global _warming
    if not _warming and _popular is None:
        _warming = True
        threading.Thread(target=popular, daemon=True).start()


def _resolve_part(required: set, size: str, has_category: bool):
    """(part, candidates, message): a part only when exactly one reading fits; never a silent pick."""
    scored = _rank(_index().rows, required, size)
    if not scored:
        return None, [], "No part in the library matches"
    top = scored[0]
    if has_category:
        rivals = [s for s in scored[1:] if s[0][0] == top[0][0]]
    else:                           # no shape word: a brick, a plate and a tile of one size are different readings
        rivals = [s for s in scored[1:6] if s[3] != top[3] or s[0][0] == top[0][0]]
    if all(_stem(s[1]) == _stem(top[1]) for s in rivals):       # variants of one mould (3062a, 3062b): take the plain one
        also = [s[1] for s in rivals][:3]
        return top[1], [], ("also fits " + ", ".join(also)) if also else ""
    return None, [{"part": s[1], "description": s[2], "category": s[3]} for s in scored[:MAX_CANDIDATES]], \
        f"{len(scored)} parts fit; pick one"


def parse_line(line: str, table: dict[str, int] | None = None) -> dict:
    table = table if table is not None else colour_table()
    index = _index()
    text = line.casefold().replace("×", "x")
    item: dict = {"line": line.strip(), "qty": None, "qty_assumed": False, "colour": None, "colour_name": "", "colour_candidates": [],
                  "part": None, "description": "", "category": "", "candidates": [], "status": "needs_part", "message": "", "notes": []}
    notes = item["notes"]
    qty, size, part_id = None, "", None

    # "3 x 3001": a quantity, then a part number that cannot be a size (sizes stop at 48 studs)
    m = re.match(r"\s*(\d+)\s*x\s*([0-9]+[a-z]*)\b(?!\s*x\s*\d)", text)
    if m and m.group(2) in index.meta and (int(_stem(m.group(2))) > 48 or len(m.group(2)) > 3):
        qty, part_id = int(m.group(1)), m.group(2)
        text = text[m.end():]
    else:
        m = re.match(r"\s*(\d+)\s*x\b(?!\s*\d)\s*", text)           # "20x red brick"
        if m:
            qty, text = int(m.group(1)), text[m.end():]
        m = re.search(r"\bx\s*(\d+)\s*$", text)                     # "red brick x20"
        if m and qty is None:
            qty, text = int(m.group(1)), text[:m.start()]
        m = part_info.SIZE_RE.search(text)
        if m:
            size = part_info.size_from_match(m)
            text = text[:m.start()] + " " + text[m.end():]

    tokens = [SYNONYMS.get(w, w) for w in _words(text)]
    tokens = [w for w in tokens if w and w not in STOP]
    colour_words = set(" ".join(table).split()) | {"gray", "bluish"}
    for i in range(len(tokens) - 1):                                # Italian order: "grigio scuro" -> "dark gray"
        if tokens[i + 1] in MODIFIERS and tokens[i] in colour_words and tokens[i] not in MODIFIERS:
            tokens[i], tokens[i + 1] = tokens[i + 1], tokens[i]
    numbers = [i for i, w in enumerate(tokens) if w.isdigit()]
    if part_id is None and numbers:
        first = numbers[0]
        non_colour = [w for w in tokens if not w.isdigit() and w not in colour_words]
        if first == 0 and len(numbers) == 1 and not non_colour and not size and tokens[0] in index.meta:
            part_id = tokens.pop(0)                                 # "3001 blue": just a part number
        elif first == 0:
            qty = int(tokens.pop(0))
            numbers = [i for i, w in enumerate(tokens) if w.isdigit()]
            if numbers:
                part_id = tokens.pop(numbers[0])
        else:
            part_id = tokens.pop(first)
            numbers = [i for i, w in enumerate(tokens) if w.isdigit()]
            if numbers and qty is None:
                qty = int(tokens.pop(numbers[0]))
    if part_id is None:                                             # a part number with letters: 3001b, 3626bpb0001
        for i, w in enumerate(tokens):
            if re.fullmatch(r"\d+[a-z][a-z0-9]*", w) and w in index.meta:
                part_id = tokens.pop(i)
                break

    vocabulary = sorted(colour_words | set(SHAPES))                 # typo repair toward known words only, always reported
    for i, w in enumerate(tokens):
        known = w in colour_words or w in index.vocab or w in SHAPES or _singular(w, index.vocab) in index.vocab
        if not known and not w.isdigit():
            fixed = _fuzzy(w, vocabulary)
            if fixed:
                notes.append(f'read "{w}" as "{fixed}"')
                tokens[i] = fixed
    found = _find_colour(tokens, table)
    if found:
        start, count, code, codes = found
        del tokens[start:start + count]
        if code is not None:
            item["colour"], item["colour_name"] = code, part_info.colour_name(code)
        else:
            item["colour_candidates"] = [{"colour": c, "name": part_info.colour_name(c)} for c in codes]
    words = [_singular(w, index.vocab) for w in tokens if not w.isdigit()]

    if qty is None:
        qty, item["qty_assumed"] = 1, True
    if not 1 <= qty <= MAX_QTY:
        item["message"] = f"Quantity {qty} is out of range (1 to {MAX_QTY})"
        return item
    item["qty"] = qty

    if part_id is not None:
        if part_id not in index.meta:
            item["message"] = f'No part numbered "{part_id}" in the LDraw library'
        else:
            moved = re.match(r"~moved to ([0-9a-z]+)", index.meta[part_id][0], re.IGNORECASE)
            if moved and moved.group(1).casefold() in index.meta:
                notes.append(f"{part_id} is now {moved.group(1).casefold()}")
                part_id = moved.group(1).casefold()
            item.update(part=part_id, description=index.meta[part_id][0], category=index.meta[part_id][1])
            if words:
                notes.append("ignored: " + " ".join(words))
    else:
        unknown = [w for w in words if w not in index.vocab and w not in SHAPES]
        if unknown:
            item["message"] = ("I do not know the word " + ", ".join(f'"{w}"' for w in unknown)
                               + ". Use a part number, or words like brick, plate or tile")
        elif not words and not size:
            item["message"] = "Say which part: a part number, or a size and shape such as 2x4 brick"
        else:
            part, candidates, message = _resolve_part(set(words), size, has_category=bool(set(words) & CATEGORY_WORDS))
            item["candidates"] = candidates
            if part:
                item.update(part=part, description=index.meta[part][0], category=index.meta[part][1])
                if message:
                    notes.append(message)
            else:
                item["message"] = message
    if item["part"] is None:
        item["status"] = "needs_part"
    elif item["colour"] is None:
        item["status"] = "needs_colour"
        item["message"] = "Which grey? Pick a colour" if item["colour_candidates"] else "Which colour?"
    else:
        item["status"] = "ok"
        item["message"] = ""
    return item


def parse(text: str) -> dict:
    """{"items": [...], "ok": n, "total": n}: one entry per clause; nothing is added until the person confirms."""
    if not isinstance(text, str) or not text.strip():
        return {"items": [], "ok": 0, "total": 0}
    table = colour_table()
    items = [parse_line(c, table) for c in _split_clauses(text[:MAX_TEXT])[:MAX_LINES]]
    return {"items": items, "ok": sum(1 for i in items if i["status"] == "ok"), "total": len(items)}
