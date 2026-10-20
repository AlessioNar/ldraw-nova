"""History masking of old tool output and batched image eviction (history.llm_history)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from history import KEEP_IMAGE_MESSAGES, llm_history


def call(cid: str, name: str = "run_shell", **args) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def chat(steps: int, size: int = 5000, name: str = "run_shell") -> list[dict]:
    """A user turn then `steps` assistant steps, each calling one tool that returns `size` characters."""
    out = [{"id": 0, "role": "user", "content": "go"}]
    for i in range(1, steps + 1):
        out.append({"id": 2 * i - 1, "role": "assistant", "content": None, "tool_calls": [call(f"c{i}", name, cmd=f"ls {i}")]})
        body = "\n".join(f"line {j} of step {i}" for j in range(size // 20))
        out.append({"id": 2 * i, "role": "tool", "tool_call_id": f"c{i}", "name": name, "content": body})
    return out


def tool_out(history_: list[dict]) -> dict[str, str]:
    return {m["tool_call_id"]: m["content"] for m in history_ if m["role"] == "tool"}


@pytest.fixture
def masking(monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT", "1")
    monkeypatch.setenv("LDRAW_NOVA_MASK_AFTER_STEPS", "10")
    monkeypatch.setenv("LDRAW_NOVA_MASK_MIN_CHARS", "2000")
    monkeypatch.setenv("LDRAW_NOVA_MASK_BATCH_STEPS", "5")


def masked(out: dict[str, str]) -> set[str]:
    return {cid for cid, c in out.items() if c.startswith("[Output of")}


def test_masking_is_off_by_default():
    stored = chat(40)
    assert masked(tool_out(llm_history(stored, False, Path))) == set()


def test_masks_only_old_and_large_results(masking):
    stored = chat(30)
    stored[2 * 3]["content"] = "short"  # step 3 result is small
    out = tool_out(llm_history(stored, False, Path))
    # 30 steps, N=10, K=5: boundary = (30-10)//5*5 = 20 -> steps 1..20 are masked except the small one
    assert masked(out) == {f"c{i}" for i in range(1, 21)} - {"c3"}
    assert out["c3"] == "short"
    assert all(not out[f"c{i}"].startswith("[Output") for i in range(21, 31))


def test_nothing_masked_before_n_steps(masking):
    assert masked(tool_out(llm_history(chat(14), False, Path))) == set()


def test_boundary_advances_in_batches_and_stubs_are_byte_stable(masking):
    stored = chat(40)
    seen: dict[str, str] = {}
    changed_steps = []
    prev: set[str] = set()
    for n in range(1, 40):
        # the request at step n+1 only knows the first n steps
        upto = 1 + 2 * n
        out = tool_out(llm_history(stored[:upto], False, Path))
        now = masked(out)
        assert prev <= now
        if now != prev:
            changed_steps.append(n)
        for cid in now:
            assert seen.setdefault(cid, out[cid]) == out[cid]  # byte-identical on every later step
        prev = now
    assert changed_steps == [15, 20, 25, 30, 35]               # once per K=5, not every step


def test_stub_content(masking):
    stored = chat(30, size=8000)
    out = tool_out(llm_history(stored, False, Path))["c1"]
    original = stored[2]["content"]
    assert out.startswith("[Output of run_shell omitted")
    assert f"{len(original):,} characters" in out
    assert '"cmd": "ls 1"' in out
    assert "line 0 of step 1" in out and original.splitlines()[-1] in out
    assert "re-run run_shell" in out and len(out) < 1000
    assert "re-read it with read_file" in tool_out(llm_history(chat(30, name="read_file"), False, Path))["c1"]


def test_stored_history_is_untouched(masking):
    stored = chat(30)
    before = copy.deepcopy(stored)
    llm_history(stored, False, Path)
    assert stored == before


def test_tool_pairing_preserved(masking):
    stored = chat(30)
    out = llm_history(stored, False, Path)
    ids = [tc["id"] for m in out if m["role"] == "assistant" for tc in m["tool_calls"]]
    assert ids == [m["tool_call_id"] for m in out if m["role"] == "tool"]
    for a, t in zip(out[1::2], out[2::2]):
        assert a["tool_calls"][0]["id"] == t["tool_call_id"]


def test_interrupted_calls_still_repaired_when_masking(masking):
    stored = chat(30)
    stored.append({"id": 99, "role": "assistant", "content": None, "tool_calls": [call("dangling")]})
    stored.append({"id": 100, "role": "user", "content": "again"})
    out = llm_history(stored, False, Path)
    assert tool_out(out)["dangling"].startswith("Error: the tool call was interrupted")


def test_stub_never_longer_than_original(masking, monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_MASK_MIN_CHARS", "200")
    stored = chat(30)
    for m in stored:
        if m["role"] == "tool":
            m["content"] = "x" * 250                        # over the threshold, but the stub would not be smaller
    assert masked(tool_out(llm_history(stored, False, Path))) == set()


# --- image eviction ---------------------------------------------------------

def renders(n: int, tmp_path: Path) -> tuple[list[dict], object]:
    stored = []
    for i in range(n):
        (tmp_path / f"r{i}.png").write_bytes(b"\x89PNG fake")
        stored.append({"id": i, "role": "user", "content": f"render {i}", "_images_for_llm": [f"r{i}.png"]})
    return stored, lambda ref: tmp_path / ref


def shown(stored, resolve) -> list[str]:
    return [m["content"][0]["text"] for m in llm_history(stored, True, resolve)]


def test_image_eviction_batches(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_IMAGE_EVICTION_BATCH", "4")
    stored, resolve = renders(12, tmp_path)
    counts = []
    prefixes_changed = 0
    prev: list[str] = []
    for n in range(1, 13):
        cur = shown(stored[:n], resolve)
        counts.append(len(cur))
        assert cur[-1] == f"render {n - 1}"                       # the newest is always there
        assert len(cur) >= min(n, KEEP_IMAGE_MESSAGES)
        if prev and cur[:len(prev)] != prev:
            prefixes_changed += 1
        prev = cur
    assert counts == [1, 2, 3, 4, 5, 2, 3, 4, 5, 2, 3, 4]
    assert prefixes_changed == 2                                  # one rewrite per batch, not one per render


def test_image_eviction_keeps_superset_of_one_at_a_time(tmp_path: Path, monkeypatch):
    stored, resolve = renders(15, tmp_path)
    for n in range(1, 16):
        monkeypatch.setenv("LDRAW_NOVA_IMAGE_EVICTION_BATCH", "1")
        old = set(shown(stored[:n], resolve))
        for batch in ("2", "4", "7"):
            monkeypatch.setenv("LDRAW_NOVA_IMAGE_EVICTION_BATCH", batch)
            assert old <= set(shown(stored[:n], resolve))


def test_batch_of_one_is_the_old_behaviour(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LDRAW_NOVA_IMAGE_EVICTION_BATCH", "1")
    stored, resolve = renders(6, tmp_path)
    assert shown(stored, resolve) == ["render 4", "render 5"]


def test_no_images_without_vision(tmp_path: Path):
    stored, resolve = renders(3, tmp_path)
    assert llm_history(stored, False, resolve) == []
