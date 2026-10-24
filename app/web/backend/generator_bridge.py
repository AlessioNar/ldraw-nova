"""The generator registry as data, for the backend process.

Templates (models turned into parametric starting points, ldraw_tools/templates) are listed next to the generators
with ids "template:<id>"; the build worker dispatches on that prefix, so the decision engine treats both the same.

The generators live in the toolkit interpreter (they need the ldraw library), which the backend does not import.
One short subprocess dumps the registry metadata (ids, kinds, parameter schemas, colour names, digest); the result is
kept in memory and in CONFIG_DIR/generators.json, keyed on the generator files' mtimes, so a restart costs nothing and
only a toolkit update triggers another dump. Building a plan stays with the build worker; the decision engine
(fast_path.py) validates and clamps parameters against these schemas without calling the toolkit.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import settings

log = logging.getLogger("ldraw-nova.generator-bridge")

_DUMP = (
    "import json\n"
    "from ldraw_tools import generators as g\n"
    "from ldraw_tools import templates as t\n"
    "from ldraw_tools.generators.colours import NAMES\n"
    "entries = [x.entry() for x in g.REGISTRY.values()]\n"
    "entries += [dict(x.entry(), id='template:' + x.id) for x in t.REGISTRY.values()]\n"
    "digest = g.digest() + ('\\n' + t.digest() if t.REGISTRY else '')\n"
    "print(json.dumps({'digest': digest, 'entries': entries, 'colours': sorted(NAMES)}))\n"
)
_memory: Optional[dict] = None
_lock: Optional[asyncio.Lock] = None


def _signature() -> str:
    root = settings.TOOLKIT_DIR / "ldraw_tools"
    try:
        files = sorted((root / "generators").glob("*.py")) + sorted((root / "data" / "templates").glob("*.json"))
        return ",".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in files)
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
