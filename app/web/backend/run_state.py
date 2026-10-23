"""Live state of a chat's running turn: events, subscribers and approvals."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from budget import BudgetLedger


@dataclass
class Run:
    chat_id: str
    task: Optional[asyncio.Task] = None
    draft: str = ""
    tools_running: dict[str, dict] = field(default_factory=dict)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    options: dict = field(default_factory=dict)
    approvals: dict[str, tuple[dict, asyncio.Future]] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)
    phase: str = "Preparing the build workspace"
    ledger: BudgetLedger = field(default_factory=BudgetLedger)
    workers: dict[str, dict] = field(default_factory=dict)     # this turn's delegated workers, for the reconnect snapshot

    def emit(self, event: str, data: dict) -> None:
        self.last_event_at = time.time()
        if event == "progress":
            self.phase = data["summary"]
        elif event == "tool_start":
            data.setdefault("started_at", time.time())
            self.phase = {
                "run_toolkit": "Running LDraw Nova",
                "run_python": "Running the model generator",
                "run_shell": "Running a build command",
                "publish_model": "Checking and publishing the model",
                "view_image": "Reviewing a rendered image",
                "read_file": "Reading build instructions or reports",
                "write_file": "Saving build files",
                "list_files": "Inspecting available files",
            }.get(data.get("name"), self.phase)
        elif event == "worker_start":
            self.workers[data["id"]] = {**data, "status": "running", "steps": 0, "eur": 0.0}
        elif event in ("worker_step", "worker_end") and data.get("id") in self.workers:
            worker = self.workers[data["id"]]
            worker["steps"] += event == "worker_step"
            worker.update({k: v for k, v in data.items() if k in ("eur", "status")})
            data["steps"] = worker["steps"]
        elif event == "done":
            self.workers = {}
        elif event == "tool_output":
            tool = self.tools_running.get(data.get("id"))
            if tool is not None:
                tool["output"] = (tool.get("output", "") + data.get("delta", ""))[-12000:]
        for queue in list(self.subscribers):
            if queue.full():
                # Disconnect slow consumers; their reconnection receives a fresh
                # state snapshot rather than an unbounded backlog of command logs.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(("reconnect", {}))
                self.subscribers.discard(queue)
            else:
                queue.put_nowait((event, dict(data)))

    def activity(self) -> dict:
        return {"started_at": self.started_at, "last_event_at": self.last_event_at, "phase": self.phase}


_runs: dict[str, Run] = {}


def is_running(chat_id: str) -> bool:
    run = _runs.get(chat_id)
    return bool(run and run.task and not run.task.done())



async def cancel(chat_id: str) -> bool:
    run = _runs.get(chat_id)
    if not run or not run.task or run.task.done():
        return False
    run.task.cancel()
    try:
        await run.task
    except asyncio.CancelledError:
        pass
    return True


async def subscribe(chat_id: str) -> AsyncIterator[tuple[str, dict]]:
    run = _runs.get(chat_id)
    if run is None or not is_running(chat_id):
        yield "snapshot", {"running": False, "draft": "", "tools": []}
        return
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    run.subscribers.add(queue)
    try:
        yield "snapshot", {"running": True, "draft": run.draft, "tools": list(run.tools_running.values()),
                           "approvals": [a for a, _ in run.approvals.values()], "activity": run.activity(),
                           "workers": list(run.workers.values())}
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=5)
            except asyncio.TimeoutError:
                yield "activity", run.activity()
                continue
            yield event, data
            if event in {"done", "reconnect"}:
                return
    finally:
        run.subscribers.discard(queue)



def decide(chat_id: str, approval_id: str, approved: bool) -> bool:
    run = _runs.get(chat_id)
    pending = run.approvals.get(approval_id) if run else None
    if not pending or pending[1].done():
        return False
    pending[1].set_result(approved)
    return True
