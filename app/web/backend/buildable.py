"""Which bundled set models the user's inventories can build: ranked by the toolkit's `inventory buildable`.

The ranking needs every bundled model's physical BOM. The toolkit caches those on disk; building the
cache the first time takes minutes, far past a request. So the first call (or an explicit warm) starts
one background job that runs the CLI in bounded rounds, each resuming where the last stopped, and the
list route answers 202 with the job's progress until the cache is complete. With a warm cache the CLI
only reads it (seconds) and ranks. Nothing starts at server startup.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil

import collection_store
import inventories
import sandbox
import settings
import toolkit
from inventories import InventoryError

log = logging.getLogger(__name__)

MAX_LIMIT = 100
DEFAULT_LIMIT = 20
DEFAULT_MIN_PARTS = 20
MAX_QUERY_CHARS = 100
RANK_TIMEOUT = 180
BUILD_ROUND_SECONDS = 600           # the CLI's own budget per round
BUILD_ROUND_TIMEOUT = BUILD_ROUND_SECONDS + 300
MAX_ROUNDS = 30
PROGRESS = re.compile(r"buildable: (\d+)/(\d+) models")

_status: dict = {"state": "idle", "done": 0, "total": 0}
_error: str | None = None
_job: asyncio.Task | None = None
_ranking = asyncio.Semaphore(2)


def status() -> dict:
    """{state: idle|building|ready, done, total} (+ error when the last build failed)."""
    return {**_status, **({"error": _error} if _error else {})}


def reset() -> None:
    """Forget the job state (tests)."""
    global _job, _error
    if _job is not None and not _job.done():
        _job.cancel()
    _job, _error = None, None
    _status.update(state="idle", done=0, total=0)


async def _cli(args: list[str], timeout: int, on_output=None) -> dict:
    """Runs `inventory buildable ...` in a scratch folder; the JSON report goes to a file, not the sandbox's stdout."""
    work = collection_store.scratch_dir(".buildable-")
    try:
        work.chmod(0o755)
        sandbox.give_to_agent(work)
        argv = ["bash", "-c", 'exec "$@" > report.json', "buildable", str(settings.TOOLKIT_PYTHON), "-m", "ldraw_tools.cli",
                "inventory", "buildable", *args, *collection_store.cli_store_args(), "--json"]
        result = await sandbox.run(argv, work, timeout=timeout, env_extra=toolkit.environment(), on_output=on_output)
        if result.timed_out:
            raise InventoryError(504, "Ranking the models took too long")
        try:
            report = json.loads((work / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("buildable failed (exit %s): %s %s", result.exit_code, exc, result.stderr[-2000:])
            raise InventoryError(500, "The toolkit could not rank the models") from None
        if result.exit_code != 0 or "results" not in report:
            log.error("buildable failed (exit %s): %s %s", result.exit_code, report.get("error"), result.stderr[-2000:])
            raise InventoryError(500, "The toolkit could not rank the models")
        return report
    finally:
        await asyncio.to_thread(shutil.rmtree, work, True)


def _progress(_channel: str, text: str) -> None:
    seen = PROGRESS.findall(text)
    if seen:
        _status.update(done=int(seen[-1][0]), total=int(seen[-1][1]))


async def _warm() -> None:
    global _error
    try:
        last = -1
        for _ in range(MAX_ROUNDS):
            report = await _cli(["--warm", f"--max-build-seconds={BUILD_ROUND_SECONDS}"], BUILD_ROUND_TIMEOUT, _progress)
            _status.update(done=report["cached"], total=report["models"])
            if not report["pending"]:
                _status["state"] = "ready"
                return
            if report["cached"] <= last:
                raise InventoryError(500, "The model cache stopped making progress")
            last = report["cached"]
        raise InventoryError(500, "The model cache did not finish building")
    except asyncio.CancelledError:
        _status["state"] = "idle"
        raise
    except Exception as exc:
        log.error("buildable cache build failed: %s", exc)
        _error = str(exc) if isinstance(exc, InventoryError) else "The model cache could not be built"
        _status["state"] = "idle"


def warm() -> dict:
    """Starts the cache build unless it is running (or done); returns the status."""
    global _job, _error
    if _status["state"] == "idle":
        _error = None
        _status["state"] = "building"
        _job = asyncio.get_running_loop().create_task(_warm())
    return status()


def clean_ids(ids: str | None) -> list[str]:
    wanted = list(dict.fromkeys(i.strip() for i in (ids or "").split(",") if i.strip()))
    if not wanted:
        wanted = collection_store.source_ids()
        if not wanted:
            raise InventoryError(400, "Upload a parts inventory first")
    if len(wanted) > inventories.MAX_IDS:
        raise InventoryError(400, f"Choose at most {inventories.MAX_IDS} inventories")
    for i in wanted:
        if not collection_store.exists(i):
            raise InventoryError(404, "no such inventory")
    return wanted


async def ranked(ids: list[str], *, limit: int = DEFAULT_LIMIT, min_parts: int = DEFAULT_MIN_PARTS, max_missing_share: float | None = None,
                 query: str = "", substitute: bool = False) -> dict | None:
    """The toolkit's ranked report, or None while the model cache is still being built (see status())."""
    if _status["state"] == "building":
        return None
    if _error:
        raise InventoryError(500, _error)
    args = [",".join(ids), f"--limit={max(1, min(limit, MAX_LIMIT))}", f"--min-parts={max(0, min_parts)}", "--cache-only"]
    if max_missing_share is not None:
        args.append(f"--max-missing-share={max_missing_share}")
    if query.strip():
        args.append(f"--query={query.strip()[:MAX_QUERY_CHARS]}")
    if substitute:
        args.append("--substitute-colours")
    args.append("--available")          # rank what is free: owned minus built sets and reservations
    async with _ranking:
        report = await _cli(args, RANK_TIMEOUT)
    if report["pending"]:
        _status.update(state="idle", done=report["cached"], total=report["models"])
        warm()
        return None
    _status.update(state="ready", done=report["cached"], total=report["models"])
    return report
