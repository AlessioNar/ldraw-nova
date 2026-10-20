# Fix: history carrying (REPORT findings 3 and 5)

Code: `app/web/backend/history.py` (`llm_history`), `settings.py` (flags), `tools.py` (`t_read_file` cap only).
Tests: `app/web/backend/tests/test_history.py`. Offline numbers: `docs/plans/loop-perf/replay_history.py`.

## What changed

### Observation masking of old tool output (finding 3) - default OFF
`llm_history` can replace a tool result by a stub in the **outbound copy only** (the stored messages are never modified).
A result is masked when all of these hold:
- the step that issued the call is at or before a boundary, where `boundary = (steps - N) // K * K` and `steps` is the number of assistant steps in the history. So a result is at least N steps old when it is masked, and the boundary advances only every K steps, in a batch.
- its content is a string longer than M characters.
- the stub is shorter than the original (otherwise it is left alone).

The stub is a pure function of (tool name, call arguments, content), so it is byte-identical on every later request. Example shape:

```
[Output of read_file omitted to save context: 120,068 characters, 3,102 lines. Arguments: {"path": "data/categories/parts/others.py"}
First lines:
<4 lines, each cut to 160 chars>
... [3,094 lines omitted] ...
Last lines:
<4 lines>
The full output is not in this conversation any more; if you still need it, re-read it with read_file (use offset and max_chars to get the part you need).]
```
For other tools the last sentence says to re-run the tool with the same arguments and redirect big output to a file. Assistant messages and `tool_call_id` pairing are untouched; the repair of interrupted calls works as before (tested).

### Batched image eviction (finding 5) - default ON
Before: all but the newest `KEEP_IMAGE_MESSAGES = 2` render messages were dropped, so each new render rewrote one message in the middle of the history.
Now: the oldest `(n - 2) // B * B` render messages are dropped (`B = LDRAW_NOVA_IMAGE_EVICTION_BATCH`, default 4). The model sees between 2 and 1+B renders (2..5), and the rewrite happens once per B renders.

**Which case applies: default ON.** For every n and every B, the set kept by the batched rule contains the set kept by the old rule (the old rule drops `n - 2`, the new one drops `<= n - 2`; tested for B = 2, 4, 7 over n = 1..15). Nothing the model previously had is removed; it only sometimes keeps up to B-1 extra older renders (about 1-1.5k tokens each). Evicted renders are still omitted entirely (no stub), as before. A stub in the same position would not help the cache: the first changed byte is at the same place either way. `B = 1` restores the old behaviour exactly. The one test that pinned "exactly 2 images" (`test_llm_history_sends_only_newest_images`) now sets `B = 1`.

### `read_file` default cap
`t_read_file(max_chars=None)` now takes its default from `LDRAW_NOVA_READ_FILE_MAX_CHARS`; default **40000 unchanged** (min 1000, the existing 200000 hard max still applies; the schema is unchanged). Lowering it makes the model page through files with `offset`, which the system prompt already tells it to do on truncation.

## Flags (env, read through `settings.py` at call time)

| Env var | Default | Meaning |
|---|---|---|
| `LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT` | off (`1/true/yes/on` enables) | master switch for masking |
| `LDRAW_NOVA_MASK_AFTER_STEPS` (N) | 10 | min age in assistant steps |
| `LDRAW_NOVA_MASK_MIN_CHARS` (M) | 2000 (min 200) | only longer results are masked |
| `LDRAW_NOVA_MASK_BATCH_STEPS` (K) | 10 | boundary advances in chunks of K steps |
| `LDRAW_NOVA_IMAGE_EVICTION_BATCH` (B) | 4 | 1 = old one-at-a-time eviction |
| `LDRAW_NOVA_READ_FILE_MAX_CHARS` | 40000 | read_file default `max_chars` |

## Measured offline (chars/4 estimate, 5 recorded chats, `replay_history.py`)

Run: `cd app/web/backend && LDRAW_NOVA_TOOLKIT_DIR=<toolkit> python ../../../docs/plans/loop-perf/replay_history.py --cache <chats>` (about 4 min). Request size excludes the system block (identical in every scenario). "Prefix reuse" = tokens byte-identical to the previous request / previous request size, summed over steps. Break columns count steps whose prefix breaks because of a newly masked result / an evicted render / anything else. Defaults N=10, M=2000, K=10, B=4. "mask" = masking only, "images" = batched eviction only.

| chat (steps) | base sum req | mask | images | mask+images | stubs at last step (chars) | prefix reuse base / mask / images | break steps (mask/image) base -> mask -> images |
|---|---|---|---|---|---|---|---|
| 20260928-125940-5e681c (55) | 4.42M | -41.9% | +5.1% | -36.9% | 33 (28,685) | 96.2 / 88.8 / 98.5% | 0/7 -> 4/6 -> 0/1 |
| 20260929-213629-dd4aed (174) | 14.86M | -32.6% | +1.2% | -31.4% | 19 (16,650) | 98.5 / 96.7 / 99.0% | 0/27 -> 7/26 -> 0/6 |
| 20260930-201212-16f983 (140) | 11.06M | -51.0% | +0.9% | -50.1% | 40 (36,131) | 99.1 / 94.5 / 99.4% | 0/8 -> 12/7 -> 0/2 |
| 20261003-100404-9e6e55 (150) | 30.56M | -15.4% | +0.6% | -14.7% | 19 (15,033) | 97.7 / 96.2 / 98.9% | 0/11 -> 7/11 -> 0/2 |
| 20261003-110049-79b3d1 (87) | 12.68M | -34.1% | +0.5% | -33.5% | 20 (15,031) | 98.5 / 94.3 / 98.8% | 0/9 -> 4/9 -> 0/2 |

Reading it:
- Masking removes 15-51% of the summed request size (the REPORT's simulation said 15-45%; here the stub keeps 4+4 lines and the batch lag costs a little, but chat 3 gains more because it also masks results that the simulation's 350-char head/tail kept). Chats 1-3 and 5 land at 33-51%.
- Batched eviction cuts image-caused prefix breaks from 7/27/8/11/9 to 1/6/2/2/2 steps. It adds 0.5-5% request size (the extra renders), most visible in the shortest chat.
- Masking **lowers the prefix-reuse percentage**: the oldest newly masked result sits early in the history, so each batch rewrites everything after it (4-12 steps per chat at K=10). That is the price of the smaller requests; with a rolling cache breakpoint (REPORT finding 1) it costs one cache write of the already-shrunk history per batch. A larger K trades fewer rewrites for carrying old outputs longer. Whether the net is positive depends on the provider's cache pricing and cannot be seen here.
- Only masking results in `base` that are already in the history; stored chats were never altered.

## Risk: the model needs a masked output again
Moderate (as the REPORT said). The agent reuses earlier `discover` hits, part ids and file contents. If it needs a masked output it should follow the stub: re-read the file with `read_file` (offset/max_chars) or re-run the tool, which costs an extra step and new tokens; if it ignores the stub it may guess ids or repeat a query. Mitigations in the design: only results at least N=10 steps old and over 2,000 chars are touched, the stub keeps tool, arguments, size and the first/last lines, and recovery is stated in the stub itself. The model-facing system prompt is not changed (it belongs to another agent); if live testing shows stubs are ignored, a one-line mention in the system prompt is the next step. `run_shell` results whose files/ids matter later are the most likely to cause re-runs. Nothing about this has been run against a live model.

## How to validate live (needs an API key)
1. Pick a fixed multi-step prompt that builds a model with `discover`/`read_file`/`build` (at least ~40 steps), same model, same temperature settings, fresh chat each run.
2. Run it N >= 5 times each with `LDRAW_NOVA_MASK_OLD_TOOL_OUTPUT` unset and `=1` (optionally K=5 and K=20).
3. Compare: task success (build passes, requested parts present), number of steps, number of re-reads/re-runs of an already seen call (count identical `read_file` paths and identical toolkit commands after a stub), total input tokens and `cache_read`/`cache_write` from `usage_record`, wall time. Read a few transcripts for hallucinated part ids right after stubs appear.
4. For images: compare `cache_read` share with `LDRAW_NOVA_IMAGE_EVICTION_BATCH=1` vs 4 (only meaningful once a rolling breakpoint exists), and check the model still cites the latest render correctly.
5. Ship masking only if success and step count are not worse beyond run-to-run noise.

## Verification
- `tests/test_history.py`: 13 tests (off by default; only old and large results; nothing before N steps; boundary advances once per K and stubs are byte-identical on every later step; stub content and recovery text; stored history untouched; call/result pairing and interrupted-call repair; stub never longer than the original; batched eviction; superset of the old kept set; B=1 equals old; no images without vision).
- Full backend suite: 15 failed / 191 passed / 3 skipped; the same 15 image-only failures as baseline (test_agent 1, test_api 4, test_ldraw 6, test_toolkit 4), 178 + 13 new passing.
