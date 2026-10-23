"""Stray-write guard for delegated workers (docs/plans/swarm-subagents/05-isolation-permissions.md).

What IS enforced: the file tools (write_file, and path resolution with write=True) refuse any path outside the
worker's own folder (ToolContext.write_root); run_python starts with that folder as cwd.

What is NOT enforced: run_python and run_toolkit are not a jail. They run as the unprivileged `agent` user, which
owns the chat's output/ folder, so agent-written code can open any absolute path there. This module is the
mitigation, not a prevention: it hashes output/ before and after the worker (leaving out output/workers/, where
workers legitimately write) and undoes what changed, so the planner's files cannot silently change. Limits: it
detects after the fact; files over MAX_KEEP bytes are hashed but cannot be restored when modified or deleted
(they are reported as such); writes outside output/ and network access are out of scope; while workers run
concurrently the guard is shared (see Guard): one baseline for the batch, so a stray write is undone once and never
restored by another worker, but it is reported on whichever worker ends next, not necessarily its author.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import sandbox

MAX_KEEP = 2_000_000          # bytes kept in memory per file to be able to restore it


@dataclass
class Snapshot:
    root: Path
    files: dict[str, tuple[str, bytes | None]] = field(default_factory=dict)      # rel path -> (sha256, content or None)


def _tracked(root: Path):
    for p in sorted(root.rglob("*")) if root.is_dir() else []:
        rel = p.relative_to(root)
        if rel.parts[0] == "workers" or p.is_symlink() or not p.is_file():
            continue
        yield rel.as_posix(), p


def snapshot(output_dir: Path) -> Snapshot:
    snap = Snapshot(output_dir)
    for rel, p in _tracked(output_dir):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        snap.files[rel] = (hashlib.sha256(data).hexdigest(), data if len(data) <= MAX_KEEP else None)
    return snap


def restore(before: Snapshot) -> list[str]:
    """Undo changes made since `before`. Returns one human line per affected file."""
    root, report = before.root, []
    now = {rel: p for rel, p in _tracked(root)}
    for rel, p in now.items():
        if rel not in before.files:
            p.unlink(missing_ok=True)
            report.append(f"{rel} (new, deleted)")
    for rel, (digest, data) in before.files.items():
        p = root / rel
        if rel in now and hashlib.sha256(p.read_bytes()).hexdigest() == digest:
            continue
        state = "modified" if rel in now else "deleted"
        if data is None:
            report.append(f"{rel} ({state}, too large to restore)")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            sandbox.give_to_agent(p)
            report.append(f"{rel} ({state}, restored)")
    return report


def stray_line(report: list[str], worker_dir: str) -> str | None:
    if not report:
        return None
    return (f"Stray writes outside {worker_dir}/ were detected and undone: " + "; ".join(report[:20])
            + (f"; and {len(report) - 20} more" if len(report) > 20 else "")
            + ". Workers may write only inside their own folder.")


class Guard:
    """One baseline for all workers running at the same time.

    The first worker to start takes the snapshot; later ones join it. The planner is blocked while its delegate
    calls run, and workers write only under output/workers/, so any other change during the batch is a stray write
    by some worker. `check()` undoes and reports it (once: the baseline is restored, not advanced); the last worker
    to leave drops the baseline. Per-worker snapshots would let a later worker "restore" an earlier worker's stray
    write back into place.
    """

    def __init__(self) -> None:
        self._active, self._snap = 0, None

    def enter(self, output_dir: Path) -> None:
        if self._active == 0:
            self._snap = snapshot(output_dir)
        self._active += 1

    def check(self) -> list[str]:
        return restore(self._snap) if self._snap is not None else []

    def leave(self) -> None:
        self._active -= 1
        if self._active <= 0:
            self._active, self._snap = 0, None
