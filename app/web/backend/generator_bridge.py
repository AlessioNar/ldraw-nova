"""The generator registry as data, for the backend process.

Templates (models turned into parametric starting points, ldraw_tools/templates) are listed next to the generators
with ids "template:<id>"; the build worker dispatches on that prefix, so the decision engine treats both the same.

The generators live in the toolkit interpreter (they need the ldraw library), which the backend does not import.
One short subprocess dumps the registry metadata (ids, kinds, parameter schemas, colour names, digest); the result is
kept in memory and in CONFIG_DIR/generators.json, keyed on a hash of the generator, grammar and template files, so a restart costs nothing and
only a toolkit update triggers another dump. Building a plan stays with the build worker; the decision engine
(fast_path.py) validates and clamps parameters against these schemas without calling the toolkit.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Optional

import settings

log = logging.getLogger("ldraw-nova.generator-bridge")

_DUMP = (
    "import json\n"
    "from ldraw_tools import generators as g\n"
    "from ldraw_tools import templates as t\n"
    "t.refresh()\n"
    "from ldraw_tools.generators.colours import NAMES\n"
    "entries = [x.entry() for x in g.REGISTRY.values()]\n"
    "entries += [dict(x.entry(), id='template:' + x.id) for x in t.REGISTRY.values()]\n"
    "digest = g.digest() + ('\\n' + t.digest() if t.REGISTRY else '')\n"
    "print(json.dumps({'digest': digest, 'entries': entries, 'colours': sorted(NAMES)}))\n"
)
_memory: Optional[dict] = None
_lock: Optional[asyncio.Lock] = None


def _signature() -> str:
    """A content hash of everything the dump depends on. Contents, not mtimes: an image rebuild can keep old mtimes,
    and the building grammar lives outside generators/ (a stale cache once hid the "building" generator)."""
    root = settings.TOOLKIT_DIR / "ldraw_tools"
    try:
        files = sorted([*root.glob("generators/*.py"), *root.glob("grammar/**/*.py"), *root.glob("grammar/**/*.json"),
                        *root.glob("templates/*.py"), *root.glob("data/templates/*.json")])
        digest = hashlib.sha256()
        for p in files:
            digest.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes())
        for p in sorted(settings.TEMPLATES_DIR.glob("*.json")):          # the person's own templates
            digest.update(b"user/" + p.name.encode() + b"\0" + p.read_bytes())
        return digest.hexdigest()
    except OSError:
        return ""


def _cache_path():
    return settings.CONFIG_DIR / "generators.json"


def cached() -> Optional[dict]:
    """The registry if it is already in memory or on disk (no subprocess, microseconds); else None."""
    global _memory
    if _memory is None:
        try:
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
            if data.get("signature") == _signature() and data.get("entries"):
                _memory = data
        except (OSError, ValueError):
            pass
    return _memory


def set_registry(data: Optional[dict]) -> None:
    """Install a registry (tests) or drop the cached one (None)."""
    global _memory
    _memory = data


async def ensure() -> Optional[dict]:
    """The registry, dumping it from the toolkit on the first call. None when the toolkit is unavailable."""
    global _memory, _lock
    if cached() is not None:
        return _memory
    _lock = _lock or asyncio.Lock()
    async with _lock:
        if _memory is not None:
            return _memory
        try:
            proc = await asyncio.create_subprocess_exec(
                str(settings.TOOLKIT_PYTHON), "-c", _DUMP, cwd=str(settings.TOOLKIT_DIR),
                env={**os.environ, "LDRAW_NOVA_TEMPLATES_DIR": str(settings.TEMPLATES_DIR)},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            data = json.loads(out.decode())
            data["signature"] = _signature()
            _memory = data
            try:
                settings.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                _cache_path().write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001 - no toolkit (tests, broken install): the fast path simply is unavailable
            log.warning("generator registry unavailable: %s", exc)
    return _memory


_warming: Optional[asyncio.Task] = None


def warm() -> None:
    """Start dumping the registry in the background if it isn't loaded (no waiting); called at start and on a stale
    cache, so the instant hint and the decision use the current generators."""
    global _warming
    if cached() is not None or (_warming is not None and not _warming.done()):
        return
    try:
        _warming = asyncio.get_running_loop().create_task(ensure())
    except RuntimeError:          # no running loop (tests, scripts): the next ensure() call dumps
        pass

