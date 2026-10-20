# Fix for finding 1: rolling cache breakpoint, optional tail after the history

## What changed

- `system_prompt.with_rolling_breakpoint(messages, model)`: returns a copy of the request whose last message carries `cache_control: {"type":"ephemeral"}`. Only when `providers.supports_prompt_caching(model)` (other providers reject the key); skipped if the request already has 4 breakpoints. A string `content` becomes a one-block list (like `system_message`), a block list gets the key on its last block, a content-less message (assistant with only tool calls) gets it at message level. The last message is shallow-copied, so the history list, the stored chat and `messages.jsonl` never see the key. Breakpoints used: system prefix 1 + rolling 1 = 2 of 4.
- `system_prompt.outbound_messages(...)` is the one call `turn.py` makes after `bounded_history` (a 2-line change there). It applies the breakpoint and, with the flag below, builds the trailing context message.
- `system_message(..., tail_in_system=None)` leaves the tail out when the flag is on.

## Flags (settings.py)

| env | default | effect |
|---|---|---|
| `LDRAW_NOVA_ROLLING_CACHE_BREAKPOINT` | on | (a) kill switch. Only has an effect for caching models. |
| `LDRAW_NOVA_TAIL_AFTER_HISTORY` | **off** | (b) work listing, NOTES.md, mode prompt and `bounded_history`'s trim note go into a trailing message after the history instead of the system turn. Changes what the model sees; applies to all providers, caching or not (it also keeps implicit-prefix caches such as OpenAI's intact). |

### Shape of (b) and why

`[system(stable, cache_control), *history (last message: cache_control), user("[Workspace state for this step, added by the app. It is not a message from the user.]\n\n<listing, NOTES.md, mode, trim note>")]`

- Not a trailing `system` message: the system turn has to come first; LiteLLM hoists later system messages into Anthropic's `system` param (which would put the tail back in front of the history) and other providers reject them.
- A `user` message is accepted after a tool result by OpenAI-style APIs; on Anthropic LiteLLM folds it into the same user turn as the `tool_result` blocks, so there are no consecutive same-role turns on the wire (OpenAI-style providers that dislike consecutive user turns never get one: it follows an assistant or tool message, except when the history ends in a user message, the first step, where two user messages are normal for OpenAI-compatible APIs and are merged by LiteLLM for Anthropic).
- The trailer sits after the rolling breakpoint, so it is never cached or written and a changing listing cannot invalidate the history. Cost: it is re-billed at 1x each step (70 to ~2,000 tokens).
- The wrapper sentence tells the model it is app state, not user input, so it does not answer it as a request. The mode prompt (PLAN/CHAT MODE) moves with it; behaviour of plan mode at the end of context needs the live check below.

## Offline numbers

`docs/plans/loop-perf/cache_scenarios.py` replays the 5 recorded chats with the real `system_message` / `outbound_messages` / `llm_history` and bills each request against a simulated Anthropic prefix cache (read 0.1x, write 1.25x, miss 1x). Run:

    cd app/web/backend && LDRAW_NOVA_TOOLKIT_DIR=<toolkit> python ../../../docs/plans/loop-perf/cache_scenarios.py --cache <chats dir> --out <dir> [--keep-images 100000]

Assumptions: tokens are chars/4 (images by dimensions) as in the report; every identical byte hits; 5-minute TTL, the 20-block lookback and the minimum cacheable length are ignored; `bounded_history` is not applied (it never fired in these chats); string and one-block content hash alike (the provider sees both as a text block); all five chats are billed as if on a caching Anthropic model; the work folder is the report's lower-bound reconstruction. Millions of input-token cost equivalents:

| chat | steps | baseline (master) | (a) | (a) vs base | (a)+(b) | (a)+(b) vs base |
|---|---|---|---|---|---|---|
| 20260928-125940-5e681c | 55 | 4.60 | 2.36 | -49% | 1.30 | -72% |
| 20260929-213629-dd4aed | 174 | 15.52 | 5.80 | -63% | 4.89 | -68% |
| 20260930-201212-16f983 | 140 | 11.53 | 8.20 | -29% | 1.90 | -84% |
| 20261003-100404-9e6e55 | 150 | 31.06 | 8.85 | -71% | 7.34 | -76% |
| 20261003-110049-79b3d1 | 87 | 12.97 | 5.95 | -54% | 3.01 | -77% |

With image eviction switched off (`--keep-images 100000`, i.e. what finding 5 would add; render tokens are still counted):

| chat | baseline | (a) | (a)+(b) | (a) vs base | (a)+(b) vs base |
|---|---|---|---|---|---|
| 20260928-125940-5e681c | 5.04 | 1.79 | 0.81 | -64% | -84% |
| 20260929-213629-dd4aed | 17.63 | 2.89 | 2.46 | -84% | -86% |
| 20260930-201212-16f983 | 11.82 | 7.40 | 1.70 | -37% | -86% |
| 20261003-100404-9e6e55 | 31.81 | 5.01 | 3.93 | -84% | -88% |
| 20261003-110049-79b3d1 | 13.29 | 3.95 | 1.81 | -70% | -86% |

Reading it:
- These do not reproduce REPORT.md's B/C columns (e.g. 0.91M / 0.37M for chat 2). Those are below 0.1x of the chat's total tokens, which cannot happen with 174 steps of ~100k-token requests that must at least be read; the numbers here are internally consistent (each step pays at least 0.1x of its prefix). The direction (baseline > a > a+b) is the same, the size of the win is smaller: 29-71% for (a) alone, 68-84% for both.
- Remaining cost after (a)+(b) is mostly the image eviction in `llm_history` (it rewrites a message in the middle of the history, so everything behind it is re-written): finding 5. Fixing it is worth another 10-60% on top.
- (a) alone helps less on chats where the tail churns (`16f983`: every `run_python` adds a listing line): each tail change still invalidates the history. (b) is what fixes that chat (-29% to -84%).
- Caveats: a real hit rate will be lower than "every identical byte hits"; a pause longer than 5 minutes re-writes the whole history at 1.25x (no worse than 1.25x of baseline for that one step).

## Tests

`tests/test_prompt_cache.py` (new cases): breakpoint on the last outbound message for a caching model and absent for others or when disabled; history input not mutated; at most 4 breakpoints (a pre-marked request is left alone); string, block-list and content-less last messages; the stable prefix block byte-identical across steps while the work folder and NOTES.md change and the older history is byte-identical apart from the moved mark; default shape unchanged; shape with `TAIL_AFTER_HISTORY` for a caching and a non-caching model (system holds only the stable text, trailing user message, trim note in the trailer, rolling mark before the trailer).

## Risks

- (a) is a pure billing change but costs 1.25x on each new tail written: if the chat pauses past the TTL or the tail churns every step the bill can exceed no-caching for those steps. Provider quirks: LiteLLM forwarding `cache_control` inside a `tool` message's block list (Anthropic and OpenRouter/Anthropic expected; verify live), and models behind gateways that report `supports_prompt_caching` but ignore the key (then just no gain).
- (b) changes model-visible context: the model sees the listing, NOTES.md and plan/chat mode at the end. Hand-over behaviour on resumed chats and plan-mode compliance must be checked live before turning it on. Also, when a trim happens the note moves from the system turn to the trailer.
- The `claude_agent` (browser login) path is untouched; it uses `system_prompt()` as before.

## Live validation (needs an API key; nothing here was run against a provider)

1. Pick a caching model (e.g. an `anthropic/claude-*` entry) and a fixed prompt that makes the agent take at least 15 steps (e.g. the cottage prompt from the recorded chats). Run the same prompt three times in fresh chats: master, this branch with defaults, this branch with `LDRAW_NOVA_TAIL_AFTER_HISTORY=1`.
2. Per assistant message read `_usage` in `messages.jsonl` (from `usage_record`): `cache_read_input_tokens`, `cache_creation_input_tokens`, `input_tokens`. Expected with (a): from step 2 on `cache_read` approximately the previous request's size (not just the ~20k system prefix), `cache_creation` only the new tail (a few k), and drops to the system-prefix size only on steps where the work listing/NOTES.md changed; with (b) those drops disappear (except after image eviction). Compute cost-equivalents as `input*1 + creation*1.25 + read*0.1` per chat and compare. Check that no request is rejected (400 on `cache_control`, consecutive-role errors) also on an OpenAI-style and an OpenRouter model with (b) on.
3. Quality, with (b) on: same fixed prompt, compare success of the build, step count, and whether the model uses NOTES.md/the listing after a resumed chat (start a new turn in an existing chat with a NOTES.md) and obeys plan and chat mode.
4. Confirm a 4-breakpoint limit is never hit (no provider error) and that `chat.json`/`messages.jsonl` contain no `cache_control`.
