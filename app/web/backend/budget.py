"""Shared euro budget for a chat's planner and its delegated workers (docs/plans/swarm-subagents/04-shared-budget.md).

Worker steps are not saved in the chat while they run, so the ledger holds what they have spent (`spent`) and
what each running worker may still spend (its reservation). A finished worker's cost stays in the ledger until
a saved chat message carries its `_worker` id; from then on `usage_totals` counts it and the ledger does not,
so nothing is counted twice and nothing is missed between the end of a worker and the saving of its record.
Amounts are USD inside and converted with settings.eur_per_usd(). A lock makes it safe for threads and tasks.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

import settings
import usage


@dataclass
class _Worker:
    reserved: float            # USD the worker may spend
    spent: float = 0.0
    active: bool = True


def saved_workers(stored: list[dict]) -> set[str]:
    """Ids of workers whose usage record is already a chat message (new `_worker` dict or old `_subagent` string)."""
    ids = set()
    for message in stored:
        worker = message.get("_worker")
        if worker:
            ids.add(worker.get("id") if isinstance(worker, dict) else worker)
    return ids


class BudgetLedger:
    def __init__(self, limit_eur: float | None = None) -> None:
        self.limit_eur = limit_eur
        self._workers: dict[str, _Worker] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _usd(eur: float) -> float:
        return eur / settings.eur_per_usd()

    def _unsaved_usd(self, stored: list[dict]) -> float:
        saved = saved_workers(stored)
        return sum(w.spent for wid, w in self._workers.items() if wid not in saved)

    def _outstanding_usd(self, skip: str | None = None) -> float:
        return sum(max(w.reserved - w.spent, 0.0) for wid, w in self._workers.items() if w.active and wid != skip)

    def _remaining(self, stored: list[dict], skip: str | None = None) -> float | None:
        if not self.limit_eur:
            return None
        rate = settings.eur_per_usd()
        spent = usage.usage_totals(stored)["cost_eur"] + (self._unsaved_usd(stored) + self._outstanding_usd(skip)) * rate
        return self.limit_eur - spent

    @property
    def live_eur(self) -> float:
        """Euros spent by workers whose cost is not yet in a saved message is `planner_spent_eur`; this is all of it."""
        with self._lock:
            return sum(w.spent for w in self._workers.values()) * settings.eur_per_usd()

    def remaining_eur(self, stored: list[dict]) -> float | None:
        """Budget left after stored cost, worker spending and running workers' reservations; None without a limit."""
        with self._lock:
            return self._remaining(stored)

    def reserve(self, worker_id: str, max_eur: float, stored: list[dict]) -> float:
        """Reserve up to `max_eur` for a worker, clamped to what is left (possibly 0). Returns the euros granted."""
        with self._lock:
            left = self._remaining(stored)
            granted = max_eur if left is None else max(min(max_eur, left), 0.0)
            self._workers[worker_id] = _Worker(reserved=self._usd(granted))
            return granted

    def add(self, worker_id: str, cost_usd: float | None) -> None:
        if cost_usd:
            with self._lock:
                self._workers[worker_id].spent += cost_usd

    def release(self, worker_id: str) -> None:
        """The worker ended: drop its reservation. Its spending stays until its record is saved in the chat."""
        with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].active = False

    def spent_eur(self, worker_id: str) -> float:
        with self._lock:
            return self._workers[worker_id].spent * settings.eur_per_usd()

    def planner_spent_eur(self, stored: list[dict]) -> float:
        """What the planner's budget check compares with the limit: stored cost plus unsaved worker cost."""
        with self._lock:
            return usage.usage_totals(stored)["cost_eur"] + self._unsaved_usd(stored) * settings.eur_per_usd()

    def chat_exhausted(self, worker_id: str, stored: list[dict]) -> bool:
        """True when stored + worker spending + the other running workers' reservations reach the limit."""
        with self._lock:
            left = self._remaining(stored, skip=worker_id)
            return left is not None and left <= 0


@dataclass
class WorkerLimits:
    """Per-turn delegation state shared by every ToolContext copy of a turn: the concurrency semaphore (made on
    first use, inside the running loop), the number of delegations started and the shared stray-write guard."""
    delegations: int = 0
    guard: Any = None
    _sem: asyncio.Semaphore | None = field(default=None, repr=False)
    _loop: Any = field(default=None, repr=False)

    def semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            self._sem, self._loop = asyncio.Semaphore(settings.max_workers()), loop
        return self._sem
