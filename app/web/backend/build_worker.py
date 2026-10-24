"""A long-lived build worker: the toolkit's pipeline (build, geometry, GLB, renders) in one process.

Each toolkit CLI call pays 15-25 s of start-up; the worker pays it once and keeps the parts loaded. It is
started like every sandbox command: as the unprivileged `agent` user (setpriv), with the scrubbed environment from
sandbox.py plus toolkit.environment(), no API keys, and a file-size limit. There is no CPU-time ulimit (the process
is meant to live long): a request that exceeds its timeout gets the whole process group killed instead.

Protocol (ldraw_tools/pipeline.py): one JSON object per line on stdin, one reply per line on stdout. One request
runs at a time; up to `max_queue` more wait; beyond that WorkerBusy. A crash (EOF) or timeout is reported to the
caller and the next request starts a fresh worker.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import signal
import uuid
from pathlib import Path

import sandbox
import settings
import toolkit

REQUEST_TIMEOUT = int(os.environ.get("LDRAW_NOVA_WORKER_TIMEOUT", "300"))
STARTUP_TIMEOUT = 120
MAX_QUEUE = int(os.environ.get("LDRAW_NOVA_WORKER_QUEUE", "4"))
MAX_REPLY_BYTES = 256 * 1024 * 1024


class WorkerError(Exception):
    pass


class WorkerBusy(WorkerError):
    """The queue is full; retry later."""


class WorkerCrashed(WorkerError):
    pass


class WorkerTimeout(WorkerError):
    pass


def default_argv() -> list[str]:
    return [str(settings.TOOLKIT_PYTHON), "-m", "ldraw_tools.pipeline", "--serve"]


class BuildWorker:
    def __init__(self, argv: list[str] | None = None, env_extra: dict[str, str] | None = None,
                 max_queue: int = MAX_QUEUE, timeout: int = REQUEST_TIMEOUT):
        self._argv, self._env_extra = argv, env_extra
        self.max_queue, self.timeout = max_queue, timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        self._lock = asyncio.Lock()
        self._waiting = 0
        self.starts = 0

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def stderr_tail(self) -> str:
        return "".join(self._stderr)[-2000:]

    async def _start(self, cwd: Path) -> None:
        argv = self._argv or default_argv()
        account = sandbox._agent_account()
        home = Path(account.pw_dir) if account else cwd
        env = {
            "PATH": f"{sandbox.SCRIPTS_DIR}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "XDG_RUNTIME_DIR": f"/tmp/runtime-{sandbox.AGENT_USER}" if account else os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
            **{k: os.environ[k] for k in sandbox.PASSTHROUGH_ENV if k in os.environ},
            **(toolkit.environment() if self._argv is None else {}),
            **(self._env_extra or {}),
        }
        command = ["bash", "-c", f'ulimit -f {sandbox.MAX_FILE_BYTES // 1024}; exec "$@"', "--", *argv]
        if account:
            command = ["setpriv", f"--reuid={account.pw_uid}", f"--regid={account.pw_gid}", "--clear-groups", "--", *command]
        self._stderr.clear()
        self._proc = await asyncio.create_subprocess_exec(
            *command, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=MAX_REPLY_BYTES)
        self.starts += 1
        asyncio.create_task(self._drain_stderr(self._proc))

    async def _drain_stderr(self, proc) -> None:
        while chunk := await proc.stderr.read(4096):
            self._stderr.append(chunk.decode("utf-8", "replace"))

    async def _kill(self) -> int | None:
        proc, self._proc = self._proc, None
        if proc is None:
            return None
        try:
            os.killpg(proc.pid, signal.SIGKILL)         # the whole group: leocad, node and bash children too
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            pass
        return proc.returncode

    async def close(self) -> None:
        await self._kill()

    async def request(self, payload: dict, timeout: float | None = None) -> dict:
        """Send one request, return the worker's reply. Raises WorkerBusy, WorkerCrashed or WorkerTimeout."""
        if self._waiting >= self.max_queue + 1:          # one running plus max_queue waiting
            raise WorkerBusy(f"{self.max_queue} requests are already queued; try again shortly")
        self._waiting += 1
        try:
            async with self._lock:
                return await self._exchange(payload, timeout or self.timeout)
        finally:
            self._waiting -= 1

    async def _exchange(self, payload: dict, timeout: float) -> dict:
        request_id = uuid.uuid4().hex
        line = json.dumps({**payload, "id": request_id}, ensure_ascii=False) + "\n"
        if not self.alive:
            await self._kill()
            await self._start(settings.TOOLKIT_DIR if settings.TOOLKIT_DIR.is_dir() else Path.cwd())
        proc = self._proc
        try:
            proc.stdin.write(line.encode())
            await proc.stdin.drain()
            # a fresh worker also pays start-up and library loading, so allow for it
            allowance = STARTUP_TIMEOUT if payload.get("op", "build") == "build" else 0
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout + allowance)
        except asyncio.TimeoutError:
            await self._kill()
            raise WorkerTimeout(f"the build worker did not answer within {timeout:.0f} s and was stopped") from None
        except (BrokenPipeError, ConnectionResetError):
            raw = b""
        except asyncio.CancelledError:
            await self._kill()                       # an abandoned request would leave a stale reply in the pipe
            raise
        if not raw:
            code = await self._kill()
            await asyncio.sleep(0.05)                # let the stderr drain finish
            raise WorkerCrashed(f"the build worker exited (code {code}); it restarts on the next request. {self.stderr_tail()}".strip())
        reply = json.loads(raw)
        if reply.get("id") != request_id:
            await self._kill()
            raise WorkerCrashed("the build worker answered out of step and was restarted")
        return reply


_worker: BuildWorker | None = None


def get_worker() -> BuildWorker:
    global _worker
    if _worker is None:
        _worker = BuildWorker()
    return _worker


async def shutdown() -> None:
    global _worker
    if _worker is not None:
        await _worker.close()
        _worker = None
