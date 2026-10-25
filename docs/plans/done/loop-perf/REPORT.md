# Agent loop: where tokens and time go (offline replay)

Scope: `app/web/backend` agent loop (`agent.py`, `inference.py`, `tools.py`, `sandbox.py`, `providers.py`). No source file was changed; this directory only adds the harness.

## How this was measured

- **Data**: the 5 chats that still carry `messages.jsonl` in git history (`git show b24c492c:app/data/chats/<id>/...`): 3 on Claude/GPT through the Anthropic/OpenAI path (`20260928-125940-5e681c` gpt-6-astra, 55 steps; `20260929-213629-dd4aed` claude-opus-5, 174; `20260930-201212-16f983` claude-opus-5-5, 140) and 2 on OpenRouter Qwen (`20261003-100404-9e6e55`, 150; `20261003-110049-79b3d1`, 87). 606 recorded LLM steps. The older `data/chats` set (Sep 27) is shorter and has no extra shape; not used.
- **No `_usage` exists in any of these chats** (it was added later). So nothing here is a billed number; every token figure is **chars/4** (images: w*h/750, long edge capped at 1568, read from the PNG header; user-attached JPEGs counted as a flat 1500).
- **Harness**: `replay.py` rebuilds, for each recorded assistant message, the request the live loop would have sent, by calling the real `agent.system_message` (with `providers.supports_prompt_caching`), `agent.llm_history` (so `KEEP_IMAGE_MESSAGES`, image re-attachment, per-model reasoning stripping) and `inference.bounded_history` (with the model's context window as budget). `analyze.py` turns the JSON into the tables in `analysis-output.md`. Latency comes from the recorded `created_at` stamps.
- Run: `cd app/web/backend && LDRAW_NOVA_TOOLKIT_DIR=<toolkit> python ../../../docs/plans/done/loop-perf/replay.py --out /tmp/out && python ../../../docs/plans/done/loop-perf/analyze.py /tmp/out/replay.json <cache dir printed by replay: $TMPDIR/loop-perf-chats>` (about 6 minutes, dominated by `litellm.token_counter` inside `bounded_history`; `--no-bound` skips it).
- The work folder (listing + `NOTES.md` in the system prompt) is rebuilt from `write_file`/`run_python` calls in the log. Files created by shell/toolkit commands are invisible, so the volatile-tail change counts are a **lower bound**.

## Baseline numbers

| | |
|---|---|
| Stable system prefix | 19,663 tokens (instructions.md + 5 guides), identical every step; tool schemas add ~1,035 |
| Volatile system tail | 70 to 1,991 tokens (work listing, NOTES.md up to 16k chars) |
| Request size | first ~19.5k; median 101k / 104k / 97k / 211k / 188k; max 125k / 138k / 141k / 370k / 222k |
| Sum of all requests per chat | 5.1M / 16.9M / 13.0M / 32.0M / 13.7M tokens |
| Share of re-sent tokens, non-OpenRouter chats | tool output 41-60%, system 21-22%, assistant text+args 11-35%, images 1-7% |
| Prefix reuse vs previous request | mean 81% / 96% / 70% / 92% / 86% |
| Time | LLM waits 379 min total vs 95 min in tools (run_shell 79 min over 312 calls). Median LLM wait 4-27 s, p95 65-277 s |

Images are not the problem (0.1-7% of tokens); tool output and the system prefix are.

## Top 5 findings, ranked by estimated saving

### 1. Only the stable system prefix is cached; the volatile system tail sits in front of the history and breaks the prefix

- Evidence. `agent.system_message` puts one `cache_control` breakpoint after the 19.7k-token stable block, and the history that follows is billed at full price every step. Under Anthropic multipliers (read 0.1x, write 1.25x) that design (A) saves only 18-20% versus no cache at all (e.g. `20260929-213629-dd4aed` 16.95M -> 13.89M). The byte-identical prefix from one step to the next is far larger: mean 70-96% of the previous request.
  The prefix is broken by the "Current output files" listing and NOTES.md, which `system_prompt_parts` places *before* all messages. It changed on 9 / 7 / 49 / 9 / 11 steps (per chat, order as above); each time the whole history behind it is a miss: 0.46M, 0.91M, 4.83M, 1.11M, 2.06M tokens lost (`20260930-201212-16f983` is worst: every `run_python` adds a `generators/run-*.py` line to the listing, 49 of 140 steps, 4.8M of its 13M).
  Cost counterfactual (token-equivalents): B = add a rolling breakpoint on the last message: 42-93% below A. C = B plus the volatile tail moved behind the history: 90-97% below A (4.19M -> 0.41M, 13.9M -> 0.37M, 10.5M -> 0.51M, 29.4M -> 2.7M, 12.1M -> 0.97M).
  This also matters for providers with *implicit* prefix caching (OpenAI, OpenRouter/Qwen): there stable and volatile are one string, so any listing change (9 and 11 times in the two OpenRouter chats, 9 in the GPT chat) invalidates the entire history prefix.
- Fix (low risk, two parts).
  (a) `agent.py` `_run_turn` / `system_message`: when `providers.supports_prompt_caching(model)`, also put `cache_control: {"type":"ephemeral"}` on the last content block of the last history message (convert a plain-string tool message to a one-block list, or set it at message level, which LiteLLM forwards for Anthropic). Never more than 4 breakpoints in total.
  (b) `agent.py` `system_prompt_parts`: stop putting `volatile` in the system turn. Append it as a final small user/system-reminder block after the history (or only when it changed since the last step). The stable block, tool schemas and history then form an unbroken prefix.
- Behaviour risk. (a) none to model behaviour; cache writes cost 1.25x on each new tail, so a chat that stalls beyond the 5-minute TTL pays a re-write (recorded tool gaps max 2.6 min, so TTL expiry was not an issue here). (b) is the real risk: the model sees the file listing and handover notes at the end instead of the start, and `mode_prompt` and the "earlier tool rounds omitted" note (`bounded_history` appends to the system tail) need the same treatment. Resumption behaviour from NOTES.md must be re-checked.
- Needs a live-API benchmark: yes. Cache hit rate, min-cacheable-length behaviour, write/read multipliers and TTL cannot be seen offline. Measure `cache_read`/`cache_write` from `usage_record` over a replayed-prompt run.

### 2. OpenRouter `reasoning_details` is stored one streamed delta per block and re-sent in full every step (wire payload 25x larger than it needs to be)

- Evidence. In `20261003-100404-9e6e55`, 150 assistant messages carry 325,330 `reasoning.text` blocks of ~1 token each (`{"type":"reasoning.text","text":"The","format":"unknown","index":0}`): 24.7 MB of JSON holding ~0.95 MB of actual reasoning text. `llm_history` re-sends every one on every later step, so request bodies have a median of 12.8 MB and max 25.3 MB (second chat: 4.8 / 6.8 MB). Recorded LLM wait in that chat: 159 min total, p95 277 s, 8 waits over 5 minutes (max 12.8), median 8 s in the cheapest third of steps by request size vs 36 s in the largest third (correlation only; output length confounds, fit: ~0.14 s per 1k request tokens plus ~15 s per 1k output characters).
  Coalescing adjacent same-type/format/index text blocks (offline test in `analyze.py`): 325,330 -> 150 blocks, 24.7 MB -> 1.0 MB (4.1%), and 6.3 MB -> 0.45 MB in the second chat.
  Related blind spot: `litellm.token_counter` ignores these blocks (it reports 162k tokens for a request I estimate at 365k with their text, 187k vs 222k in the other), so `bounded_history` never trimmed any step in either chat (budget 200k). Whether the provider actually bills/tokenizes prior reasoning is unknown, so the token cost is unproven; the wire size is not.
- Fix. `agent.py` `_run_turn`, where `details` is built from stream chunks: merge consecutive `reasoning.text` details with equal `type`, `format`, `index`, `id` (and no signature) by concatenating `text` before storing `_reasoning_details`. Leave encrypted/`reasoning.encrypted` and summary blocks untouched. Optionally make `bounded_history`'s `count()` add the reasoning text it ignores.
- Behaviour risk. Low-moderate: OpenRouter documents concatenation in received order, so merged text should be equivalent, but a provider that validates block boundaries/indices could reject it. Existing HTTP wire test covers the stream wrapper, not this.
- Needs a live-API benchmark: yes. Confirm the provider accepts the merged blocks, produces equivalent tool calls, and measure latency before/after on a 100+ step chat.

### 3. Old bulky tool results are carried verbatim for the rest of the chat

- Evidence. Tool output is 41-60% of what is re-sent in the three non-reasoning chats (6.8% and 1.4% in the Qwen ones, where reasoning dominates the bytes). Weighting each result by the number of later requests, `read_file` is 47% of the carrying cost, `run_shell` 22%, `run_toolkit discover` 14%. Single worst cases: `read_file data/categories/parts/others.py` (120,068 chars, ~30k tokens) in `20261003-110049-79b3d1` msg 37, carried by 77 later requests = 2.3M token-requests (17% of that chat's 13.7M); `discover` hits the sandbox cap of 20,07x chars in 10 of 10 calls (~5k tokens each, carried 120-146 steps, ~0.6-0.73M each); `build`/`inspect` also reach the cap.
  Simulation: replacing results over 2,000 chars with first/last 350 chars once they are more than 10 assistant steps old would cut the sum of requests by 40% / 29% / 45% / 15% / 32% (2.1M, 4.9M, 5.8M, 4.7M, 4.3M tokens, without caching; 19-44 results masked per chat).
- Fix. Add a pass in `inference.bounded_history`, or a new step right after `llm_history` in `agent._run_turn`, that stubs tool messages older than N steps (`"[output of <tool> omitted: 18,432 chars; re-run or read <saved path> if needed]"`), keeping head and tail. Apply it in batches (e.g. every 10 steps) so it breaks a cached prefix rarely, once per batch. Independently in `tools.py`: lower the default `t_read_file(max_chars=40_000)` (the 120 kB read shows the 200k hard max is reachable) and stop reading `docs/agent/tooling.md` (25.5 kB = 6.4k tokens) in every chat: it was read as the 1st-8th call of all three non-OpenRouter chats and carried 132-170 steps (0.8-1.1M token-requests each); `toolkit.BUILDER_GUIDES` could include it, where it is cached.
- Behaviour risk. Moderate. The agent re-uses earlier outputs (discover hits, part ids). A stub that hides them can cause re-queries (extra steps) or hallucinated ids. The existing "do not assume omitted checks passed" note shows this was already a concern for trimming.
- Needs a live-API benchmark: yes, for quality (build success rate, step count) not for tokens; the token saving is directly computable.

### 4. `bounded_history` is measured with a counter that undercounts and runs on every step (latency and safety, not tokens)

- Evidence. It deep-copies the whole message list and calls `litellm.token_counter` in a loop on every step. In this harness that call is almost all of the 6-minute replay for 606 steps, and with 25 MB request bodies it is paid before each LLM call. For the OpenRouter chats the count (162k) was under the 200k budget while the true payload is larger (finding 2), so the trim that exists to protect the context window never fired (`removed == 0` for all 606 steps; at 1M budgets in the other chats it never fires either, max 165k).
- Fix. `inference.bounded_history`: count once incrementally (cache per-message counts keyed by message identity/length) rather than recounting the whole list; add reasoning text to the count; skip `copy.deepcopy` when under budget (it only needs to copy when trimming).
- Behaviour risk. Low; only changes when trimming starts. Risk that a more accurate count trims earlier on long chats (that is the point).
- Live benchmark: not needed for the latency part (time `bounded_history` offline on the recorded chats); needed to validate any new threshold against a provider's real context error.

### 5. Image eviction (`KEEP_IMAGE_MESSAGES = 2`) rewrites the middle of the history

- Evidence. Dropping the third-newest render message changes a message in the middle of the request, so the prefix breaks there: 7 / 27 / 8 / 11 / 9 steps per chat, losing 0.16M, 0.22M, 0.10M, 0.70M, 0.19M tokens of otherwise reusable prefix (2-39% of all prefix loss per chat; largest in `20261003-100404-9e6e55`). The images themselves are only 0.1-7% of tokens, so the eviction saves little while costing cache hits once finding 1 is fixed.
- Fix. `agent.llm_history`: evict in batches (keep 2 until 6 exist, then drop down to 2), or replace evicted images with a short stable text stub at their original position, so the cut is made rarely.
- Behaviour risk. Low: the model sees up to a few more renders for a while; each is ~1-1.5k tokens.
- Live benchmark: only to confirm the cache gain after finding 1; offline numbers already show the break count.

## Needs a live-API benchmark before shipping

1. Finding 1 (a and b): cache hit rate and cost; also model behaviour from NOTES.md/listing moved to the end of the request.
2. Finding 2: provider acceptance of merged `reasoning_details`; wall-clock effect.
3. Finding 3: task quality with masked outputs (step count, successful builds).
4. Finding 5 only as part of 1.

Finding 4 can be validated offline (timing and count accuracy against a tokenizer).

## What an offline estimate cannot show

- Real billed tokens: no `_usage` was recorded in these chats; chars/4 is crude for JSON, base64-free LDraw text and reasoning. Counts could be off by 20-40%, but ratios between scenarios are more trustworthy than absolutes.
- Provider caching behaviour: minimum cacheable size, 5-minute TTL (not an issue here), 20-block lookback, hit/miss at the cache's real granularity, OpenAI/OpenRouter implicit-cache semantics. Scenario costs use Anthropic multipliers and are idealised (every identical byte is a hit); a real hit rate will be lower, but the ordering of A < B < C holds.
- Whether reasoning blocks are tokenized/billed when sent back (finding 2's token cost vs wire cost).
- Latency causality: LLM wait mixes queueing, prompt processing and output generation. Regression fits are correlation only. Tool gaps include approval waits.
- Work-folder reconstruction: changes made by shell/toolkit commands are not visible, so system-tail breaks are under-counted.
- Behaviour: whether masked outputs or relocated notes change model decisions. Five chats from three models on a single task family (building LDraw models) is a small sample; a long-context chat with many `discover`/`build` calls will weigh finding 3 more.

## Correction (from the cache fix, see FIX-cache.md)

The cache cost figures in finding 1 do not reproduce: one of them (0.91M for `dd4aed`) is below 0.1x of that chat's total, which cannot be, because every request is at least read. Re-simulated with the real code paths (`cache_scenarios.py`; read 0.1x, write 1.25x, every identical byte assumed to hit), the rolling breakpoint alone saves 29-71% per chat and with the tail moved after the history 68-84%, not 42-93% and 90-97%. The ordering of the scenarios is unchanged. Image eviction is the largest remaining prefix break (see FIX-history.md).

