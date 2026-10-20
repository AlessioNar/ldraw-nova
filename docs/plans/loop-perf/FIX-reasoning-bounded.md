# Fix: bounded_history cost and accuracy (finding 4), reasoning_details coalescing (finding 2)

## What changed

`app/web/backend/inference.py`
- `bounded_history` no longer recounts the whole history with `litellm.token_counter` on every step. `token_counter` is additive per message (a constant 3 once per call), so each message is counted alone and the count is cached by content hash (module-level LRU, 8192 entries, keyed by model). Only messages new since the previous step are tokenized. Trimming keeps the per-message sizes in step with the list, so the loop is arithmetic.
- Reasoning text is now counted: the `text`/`summary` of `reasoning_details` (or `_reasoning_details`) is joined, tokenized with `token_counter(text=...)` and added to the message. litellm skips this list-valued key. The joined text is hashed on its own so a message with 300k tiny blocks costs a join and a hash, not a `json.dumps`.
- No `copy.deepcopy` of the history. Under budget the result is a copy of each message dict without `_turn_start` (system blocks are copied too, so a caller editing the system turn cannot touch the input). Over budget only the system turn is deep-copied (it receives the "earlier rounds omitted" note); other payloads are shared with the input and never mutated.
- Fallback on tokenizer errors is unchanged (whole-list `len(json.dumps(...))`).
- New `coalesce_reasoning_details(details)` (see below).

`app/web/backend/settings.py`: `COALESCE_REASONING`, from env `LDRAW_NOVA_COALESCE_REASONING` (`1`/`true`/`yes`/`on`).
`app/web/backend/turn.py`: three lines in the block that collects `details`; if the flag is on, `details` goes through `coalesce_reasoning_details` before `_reasoning_details` is stored.

## Flags and defaults

| | default | |
|---|---|---|
| bounded_history caching, reasoning counting, no deepcopy | always on | latency and safety fix |
| `LDRAW_NOVA_COALESCE_REASONING` | off | stored `_reasoning_details` are merged only when set |

Coalescing rules: only runs of consecutive dicts with `type == "reasoning.text"`, equal `format`, `index`, `id`, a string `text`, and no other key (a `signature` or any unknown field disqualifies the block) are merged, by concatenating `text` in received order. `reasoning.encrypted`, `reasoning.summary`, other types, non-dicts and odd shapes are kept verbatim and end the run. Nothing is reordered and the input list is not mutated. The recorded blocks are all `{"type":"reasoning.text","text":...,"format":"unknown","index":0}` (404,876 blocks in the two Qwen chats, no other shape, no ids, no signatures).

## Measured numbers (offline, recorded chats, no API)

Replay wall time (`replay.py --cache ...`, whole run, 606 steps, same machine, sequential):
- before (origin/master `inference.py`): 7m23s
- after: 2m04s (the remainder is the harness's own chars/4 estimates, not `bounded_history`)

`bounded_history` alone (every 3rd step of three chats, budget 200k, 137 calls):
| chat | before | after |
|---|---|---|
| 20260929-213629-dd4aed (Claude) | 2.4 s | 0.3 s |
| 20261003-100404-9e6e55 (Qwen) | 32.0 s | 2.3 s |
| 20261003-110049-79b3d1 (Qwen) | 5.4 s | 0.5 s |

Request body bytes per step, history serialized after `llm_history` (JSON, images excluded), 150 and 87 steps:
| chat | | median | max | sum |
|---|---|---|---|---|
| 20261003-100404-9e6e55 | before | 12.76 MB | 25.22 MB | 1983.5 MB |
| | coalesced | 0.81 MB | 1.51 MB | 124.0 MB |
| 20261003-110049-79b3d1 | before | 4.75 MB | 6.71 MB | 336.1 MB |
| | coalesced | 0.76 MB | 0.91 MB | 53.3 MB |

(6.3% and 15.9% of the original size; the concatenated reasoning text is byte-identical.)

### Behaviour change: trimming now fires on the OpenRouter Qwen chats
With reasoning counted, the replay trims where the old counter never did (budget 200k): `20261003-100404-9e6e55` trims on 104 of 150 steps (first at step 46, up to 195 messages removed), `20261003-110049-79b3d1` on 58 of 87 (first at step 29, up to 128 removed). The three Claude/GPT chats are unchanged (0 trims before and after, identical output). This is intended (the true payload was above the window) but it is a real change in what Qwen sees: after the first trim the model loses older tool rounds and gets the existing "do not assume omitted checks passed" note, and because trimming moves the start of the history every few steps it also breaks provider prefix caching more often. Whether the provider tokenizes re-sent reasoning at all is unknown; if it drops it, the count is conservative. Coalescing does not change the token count (same text), so it does not change when trimming fires.

## Test evidence

`app/web/backend/tests/test_bounded_history.py` (10 tests). The old function is embedded verbatim (from `origin/master`) and compared with the new one on identical inputs, asserting equal `(messages, removed)` or equal `ValueError`, and that the input is not mutated:
- 60 random synthetic histories x 6 budgets (None, 0, 100k, 6000, 5000, 4500), with and without a cache-breakpoint system block, over 20 trimming cases; also with the tokenizer forced to fail.
- Recorded Claude/GPT chats (opt-in, `LDRAW_NOVA_RECORDED_CHATS=<dir>`, skipped otherwise; not in the repo): prefixes every 9th message, budgets 1M/60k/30k, 100+ comparisons with 10+ trimmed.
- Caching (no tokenizer call on a repeat step, one call for one new message), no deepcopy under budget, reasoning counted and able to trigger trimming (old: 0 removed).
- Coalescing: on the real recorded blocks of both Qwen chats, per message text is byte-identical, block count falls, (type, format, index) set is preserved; a synthetic case covers encrypted, summary, signed, different id/format/index, odd shapes and ordering.

Backend suite: 15 failed / 185 passed / 6 skipped; the failing IDs are exactly the baseline 15 (image-only: test_agent 1, test_api 4, test_ldraw 6, test_toolkit 4).

## Risks

- Trimming earlier on reasoning chats (above). Equivalence is only claimed for histories without reasoning blocks; with them the count is higher by design.
- The token cache is content-hash keyed, so correctness does not depend on object identity; a hash collision (128-bit blake2b) is the only theoretical miss. Memory: 8192 ints.
- Messages after trimming share nested objects with the input; nothing in `bounded_history` or the two callers mutates them.
- Coalescing is untested against a provider. OpenRouter documents concatenating reasoning text in order, but a provider might validate block count/boundaries on continuation or reject a changed block sequence. Hence default off. A flag-on chat stores merged blocks permanently; turning it off later does not re-split them (harmless: merged text is still valid input).
- Only dict details are merged; if LiteLLM ever yields objects instead, they pass through unmerged (safe no-op).

## How to validate coalescing live (needs an OpenRouter key)

1. Pick an OpenRouter reasoning model that returns `reasoning.text` details (the Qwen model of the recorded chats).
2. Run with `LDRAW_NOVA_COALESCE_REASONING=1` and start a chat that needs 10+ tool-calling steps (a build that uses `run_shell`/`run_toolkit`).
3. Pass criteria: every step after the first continues without a provider 4xx/5xx or a reasoning/format validation error; tool calls keep arriving; the chat finishes. Inspect `data/chats/<id>/messages.jsonl`: `_reasoning_details` should be a few blocks per message instead of hundreds or thousands.
4. Compare with the same task and flag off: step count to completion, build success, and answer quality should be similar; per-step latency should drop on later steps (request body ~1 MB instead of 5-25 MB). Repeat a few times; model output varies.
5. If a provider errors, turn the flag off (existing chats continue, merged blocks are still plain text) and report the provider and the error body.
