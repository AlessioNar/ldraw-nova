"""Live worker events for the UI (docs/plans/done/swarm-subagents/08-ui.md), sent through the PLANNER's emit.

A worker's own context has a silent emit on purpose, so subagents.py calls these three helpers with the
planner's context. `run_state.Run.emit` keeps the per-worker state, so a reconnecting client's snapshot has it.
Events: worker_start {id, role, task, model, max_steps, max_eur}, worker_step {id, steps, eur},
worker_end {id, status, eur}. `eur` is the worker's spending so far (the ledger's figure, the same number the
chat total later uses).
"""
from __future__ import annotations


def start(ctx, contract, model: str, cap_eur: float) -> None:
    ctx.emit("worker_start", {"id": contract.id, "role": contract.role, "task": contract.task[:200], "model": model,
                              "max_steps": contract.max_steps, "max_eur": round(cap_eur, 4)})


def step(ctx, worker_id: str) -> None:
    ctx.emit("worker_step", {"id": worker_id, "eur": round(ctx.ledger.spent_eur(worker_id), 4)})


def end(ctx, worker_id: str, status: str) -> None:
    ctx.emit("worker_end", {"id": worker_id, "status": status, "eur": round(ctx.ledger.spent_eur(worker_id), 4)})
