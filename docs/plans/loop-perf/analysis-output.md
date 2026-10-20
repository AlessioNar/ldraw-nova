## Per-chat request size (estimated tokens, chars/4)

| chat | model | steps | first req | median req | max req | sum of all requests | stable sys | volatile sys (max) |
|---|---|---|---|---|---|---|---|---|
| 20260928-125940-5e681c | gpt-6-astra | 55 | 19,467 | 101,053 | 124,708 | 5,144,876 | 19,663 | 948 |
| 20260929-213629-dd4aed | claude-opus-5 | 174 | 20,952 | 104,310 | 138,210 | 16,949,898 | 19,663 | 1,991 |
| 20260930-201212-16f983 | claude-opus-5-5 | 140 | 19,452 | 97,071 | 140,914 | 12,971,542 | 19,663 | 1,034 |
| 20261003-100404-9e6e55 | qwen/qwen3.8-27b | 150 | 19,467 | 211,412 | 370,250 | 32,002,808 | 19,663 | 70 |
| 20261003-110049-79b3d1 | qwen/qwen3.8-27b | 87 | 19,472 | 187,566 | 221,887 | 13,656,215 | 19,663 | 535 |

## Composition of what is re-sent (sum over all steps; share of total est. tokens)

| chat | system | tool output | assistant text+tool args | reasoning re-sent | user text | images |
|---|---|---|---|---|---|---|
| 20260928-125940-5e681c |  21.3% |  60.2% |  11.3% |   0.0% |   0.0% |   7.2% |
| 20260929-213629-dd4aed |  21.1% |  41.1% |  34.5% |   0.0% |   0.0% |   3.4% |
| 20260930-201212-16f983 |  21.6% |  58.8% |  18.2% |   0.0% |   0.2% |   1.1% |
| 20261003-100404-9e6e55 |   0.6% |   1.4% |   0.7% |   3.7% |   0.0% |   0.1% |
| 20261003-110049-79b3d1 |   2.0% |   6.8% |   1.1% |   5.8% |   0.0% |   0.2% |
| ALL |   2.0% |   4.9% |   2.1% |   3.7% |   0.0% |   0.3% |

## Prefix stability between consecutive requests

lcp = tokens of the byte-identical leading blocks shared with the previous request. 'ceiling' = prev request fully reusable.

| chat | steps | mean reuse of prev req | steps with >=95% reuse | system block changed (non-caching layout: stable+volatile in one string) | volatile tail changed | steps where trimming/eviction broke prefix before the last 3 blocks |
|---|---|---|---|---|---|---|
| 20260928-125940-5e681c | 55 | 81% | 38/54 | 9 | 9 | 7 |
| 20260929-213629-dd4aed | 174 | 96% | 159/173 | 0 | 7 | 14 |
| 20260930-201212-16f983 | 140 | 70% | 84/139 | 0 | 49 | 55 |
| 20261003-100404-9e6e55 | 150 | 92% | 133/149 | 9 | 9 | 7 |
| 20261003-110049-79b3d1 | 87 | 86% | 71/86 | 11 | 11 | 4 |

## Input-cost counterfactual (token-equivalents, Anthropic-style multipliers; only valid if the model supports caching)

| chat | no cache | A: current (stable-system breakpoint only) | B: + rolling breakpoint on last message | C: B + volatile tail moved behind history | B vs A | C vs A |
|---|---|---|---|---|---|---|
| 20260928-125940-5e681c | 5,144,876 | 4,194,170 | 1,461,175 | 414,379 | 65% | 90% |
| 20260929-213629-dd4aed | 16,949,898 | 13,893,285 | 905,741 | 371,076 | 93% | 97% |
| 20260930-201212-16f983 | 12,971,542 | 10,516,616 | 6,072,927 | 514,845 | 42% | 95% |
| 20261003-100404-9e6e55 | 32,002,808 | 29,370,915 | 3,985,589 | 2,705,959 | 86% | 91% |
| 20261003-110049-79b3d1 | 13,656,215 | 12,139,215 | 3,332,890 | 965,806 | 73% | 92% |

## Tool outputs (chars; est tokens = /4)

| tool[:toolkit subcommand] | calls | total chars | median | p95 | max |
|---|---|---|---|---|---|
| read_file | 58 | 595,322 | 8,336 | 25,527 | 120,068 |
| run_shell | 312 | 377,872 | 528 | 4,444 | 20,070 |
| run_toolkit:discover | 10 | 144,237 | 20,071 | 20,072 | 20,073 |
| run_python | 54 | 82,907 | 989 | 4,899 | 10,349 |
| run_toolkit:part | 5 | 37,205 | 7,984 | 10,783 | 13,480 |
| run_toolkit:catalog | 6 | 36,301 | 6,149 | 8,520 | 8,535 |
| run_toolkit:build | 1 | 20,073 | 20,073 | 20,073 | 20,073 |
| run_toolkit:inspect | 1 | 20,072 | 20,072 | 20,072 | 20,072 |
| run_toolkit:examples | 2 | 15,092 | 7,546 | 6,516 | 8,576 |
| run_toolkit:design | 2 | 13,725 | 6,862 | 696 | 13,029 |
| run_toolkit:render | 14 | 7,917 | 586 | 696 | 742 |
| view_image | 116 | 7,148 | 62 | 72 | 98 |
| run_toolkit:mechanism | 1 | 6,860 | 6,860 | 6,860 | 6,860 |
| report_progress | 13 | 5,705 | 397 | 687 | 776 |

Top 12 single tool results:

- 120,068 chars (~30,017 tok) 20261003-110049-79b3d1 msg#37 read_file data/categories/parts/others.py | # coding=utf-8 """ library/parts.py - Auto-generated Part classes for the Python ldraw pac
- 25,527 chars (~6,381 tok) 20260930-201212-16f983 msg#16 read_file docs/agent/tooling.md | # Tool reference  ## Build manuals for any atlas  ```sh ./ldraw-agent manual prepare FILE 
- 25,527 chars (~6,381 tok) 20260929-213629-dd4aed msg#8 read_file docs/agent/tooling.md | # Tool reference  ## Build manuals for any atlas  ```sh ./ldraw-agent manual prepare FILE 
- 25,527 chars (~6,381 tok) 20260928-125940-5e681c msg#7 read_file docs/agent/tooling.md | # Tool reference  ## Build manuals for any atlas  ```sh ./ldraw-agent manual prepare FILE 
- 20,073 chars (~5,018 tok) 20260930-201212-16f983 msg#37 run_toolkit:discover  | [exit code 0, 26.2s] --- stdout --- {   "query": "a Technic supercar with working steering
- 20,073 chars (~5,018 tok) 20260929-213629-dd4aed msg#82 run_toolkit:build  | [exit code 1, 34.3s] --- stdout --- {   "profile": "assembly",   "checks_passed": false,  
- 20,072 chars (~5,018 tok) 20261003-100404-9e6e55 msg#13 run_toolkit:discover  | [exit code 0, 34.6s] --- stdout --- {   "query": "a small vegetable garden with raised pla
- 20,072 chars (~5,018 tok) 20261003-100404-9e6e55 msg#12 run_toolkit:discover  | [exit code 0, 77.9s] --- stdout --- {   "query": "a large country cottage house with a roo
- 20,072 chars (~5,018 tok) 20260929-213629-dd4aed msg#63 run_toolkit:discover  | [exit code 0, 20.0s] --- stdout --- {   "query": "a repeating brick facade wall panel with
- 20,072 chars (~5,018 tok) 20260928-125940-5e681c msg#65 run_toolkit:inspect  | [exit code 0, 2.8s] --- stdout --- {   "file": "output/tidal-observatory.mpd",   "section"
- 20,072 chars (~5,018 tok) 20260928-125940-5e681c msg#14 run_toolkit:discover  | [exit code 0, 11.5s] --- stdout --- {   "query": "a historic building with a domed observa
- 20,070 chars (~5,017 tok) 20261003-100404-9e6e55 msg#31 run_toolkit:discover  | [exit code 0, 18.7s] --- stdout --- {   "query": "a vegetable garden patch with carrots an

## Latency (recorded created_at gaps)

- 20260928-125940-5e681c: LLM waits n=55 sum=30.0 min median=26.9s p95=64.6s; tool/gap sum=18.5 min; gaps>300s (cache TTL): llm=0
- 20260929-213629-dd4aed: LLM waits n=174 sum=80.0 min median=7.5s p95=115.2s; tool/gap sum=42.4 min; gaps>300s (cache TTL): llm=2
- 20260930-201212-16f983: LLM waits n=140 sum=40.4 min median=4.4s p95=85.8s; tool/gap sum=7.5 min; gaps>300s (cache TTL): llm=0
- 20261003-100404-9e6e55: LLM waits n=150 sum=159.1 min median=25.3s p95=277.4s; tool/gap sum=18.9 min; gaps>300s (cache TTL): llm=8
- 20261003-110049-79b3d1: LLM waits n=87 sum=69.2 min median=23.2s p95=100.0s; tool/gap sum=7.7 min; gaps>300s (cache TTL): llm=2

Total seconds by kind (all chats; gaps include user approval waits for tools):
- llm: 378.7 min
- tool:run_shell: 79.2 min
- tool:run_toolkit: 10.4 min
- tool:publish_model: 3.4 min
- tool:run_python: 2.0 min
- tool:view_image: 0.0 min
- tool:read_file: 0.0 min
- tool:write_file: 0.0 min
- tool:report_progress: 0.0 min
- tool:list_files: 0.0 min

## Tool results: duplicates of the system prompt, repeats, and cost of carrying them (re-send weight)

Re-send weight = est. tokens of the result x number of later requests (ignores trimming) = what carrying it costs in total.

- total tool-result re-send weight: 30,293,115 token-requests
- read_file of text already embedded in the system prompt: 0 calls, 0 tok, weight 0 (  0.0%)
- read_file of the same path+offset twice in a chat: 1 calls, weight 3,695 (  0.0%)
- weight by tool:
  - read_file: 14,282,636 ( 47.1%)
  - run_shell: 6,546,556 ( 21.6%)
  - run_toolkit:discover: 4,340,824 ( 14.3%)
  - run_toolkit:catalog: 1,019,993 (  3.4%)
  - run_python: 968,094 (  3.2%)
  - run_toolkit:part: 744,197 (  2.5%)
  - run_toolkit:build: 657,358 (  2.2%)
  - run_toolkit:examples: 399,876 (  1.3%)
- top 10 single results by weight:
  - 2,311,309  20261003-110049-79b3d1 msg#37 read_file data/categories/parts/others.py 30,017 tok x 77 later requests
  - 1,084,770  20260929-213629-dd4aed msg#8 read_file docs/agent/tooling.md 6,381 tok x 170 later requests
  - 842,292  20260930-201212-16f983 msg#16 read_file docs/agent/tooling.md 6,381 tok x 132 later requests
  - 732,628  20261003-100404-9e6e55 msg#13 run_toolkit:discover  5,018 tok x 146 later requests
  - 732,628  20261003-100404-9e6e55 msg#12 run_toolkit:discover  5,018 tok x 146 later requests
  - 707,538  20260929-213629-dd4aed msg#63 run_toolkit:discover  5,018 tok x 141 later requests
  - 697,363  20261003-100404-9e6e55 msg#31 run_toolkit:discover  5,017 tok x 139 later requests
  - 657,358  20260929-213629-dd4aed msg#82 run_toolkit:build  5,018 tok x 131 later requests
  - 602,160  20260930-201212-16f983 msg#37 run_toolkit:discover  5,018 tok x 120 later requests
  - 528,732  20260929-213629-dd4aed msg#6 read_file docs/agent/complex-models.md 3,092 tok x 171 later requests

## Reasoning blocks re-sent (OpenRouter reasoning_details)

- 20261003-100404-9e6e55: reasoning text re-sent 18.2M tok-equiv, JSON framing 1864M chars on the wire; request body median 12.8 MB, max 25.3 MB; trimmed steps: 0
- 20261003-110049-79b3d1: reasoning text re-sent 5.0M tok-equiv, JSON framing 286M chars on the wire; request body median 4.8 MB, max 6.8 MB; trimmed steps: 0

## litellm.token_counter vs this harness (what bounded_history thinks the request is)

- 20260928-125940-5e681c: budget 1,050,000; last sampled: litellm 139,746 vs harness 124,708; max litellm 139,746
- 20260929-213629-dd4aed: budget 1,000,000; last sampled: litellm 164,617 vs harness 138,210; max litellm 164,617
- 20260930-201212-16f983: budget 1,000,000; last sampled: litellm 160,290 vs harness 136,280; max litellm 160,290
- 20261003-100404-9e6e55: budget 200,000; last sampled: litellm 162,210 vs harness 364,741; max litellm 162,210
- 20261003-110049-79b3d1: budget 200,000; last sampled: litellm 151,598 vs harness 221,887; max litellm 151,598

## LLM wait vs request size (correlation only)

- 20260928-125940-5e681c: wait ~ 19.9s + -0.08s per 1k request tokens + 12.81s per 1k output chars (n=55); median wait first third of steps by size 28s, last third 25s
- 20260929-213629-dd4aed: wait ~ 40.1s + -0.18s per 1k request tokens + 3.68s per 1k output chars (n=174); median wait first third of steps by size 6s, last third 7s
- 20260930-201212-16f983: wait ~ -7.9s + 0.21s per 1k request tokens + 5.49s per 1k output chars (n=140); median wait first third of steps by size 2s, last third 8s
- 20261003-100404-9e6e55: wait ~ 13.3s + 0.14s per 1k request tokens + 14.97s per 1k output chars (n=150); median wait first third of steps by size 8s, last third 36s
- 20261003-110049-79b3d1: wait ~ 25.9s + -0.04s per 1k request tokens + 26.65s per 1k output chars (n=87); median wait first third of steps by size 18s, last third 23s

## Why the byte-identical prefix ended early (tokens that a rolling cache breakpoint would have had to re-write)

| chat | steps | lost tokens total | system_tail (work listing/NOTES.md) | image_eviction (KEEP_IMAGE_MESSAGES) | trim | other | events: sys/img/trim/other |
|---|---|---|---|---|---|---|---|
| 20260928-125940-5e681c | 55 | 1,072,288 | 910,257 | 162,031 | 0 | 0 | 9/7/0/0 |
| 20260929-213629-dd4aed | 174 | 683,101 | 464,926 | 218,175 | 0 | 0 | 7/27/0/0 |
| 20260930-201212-16f983 | 140 | 4,936,502 | 4,833,115 | 103,387 | 0 | 0 | 49/8/0/0 |
| 20261003-100404-9e6e55 | 150 | 1,809,851 | 1,112,722 | 697,129 | 0 | 0 | 9/11/0/0 |
| 20261003-110049-79b3d1 | 87 | 2,243,650 | 2,058,334 | 185,316 | 0 | 0 | 11/9/0/0 |

## Slowest tool calls (recorded gap before the tool message was saved)

- 2.6 min  20260930-201212-16f983 msg index 118 tool:run_shell
- 2.4 min  20261003-100404-9e6e55 msg index 84 tool:run_shell
- 2.4 min  20260929-213629-dd4aed msg index 308 tool:run_shell
- 1.9 min  20260929-213629-dd4aed msg index 220 tool:run_shell
- 1.8 min  20260929-213629-dd4aed msg index 222 tool:run_shell
- 1.7 min  20260928-125940-5e681c msg index 133 tool:run_shell
- 1.6 min  20261003-100404-9e6e55 msg index 75 tool:run_shell
- 1.4 min  20261003-100404-9e6e55 msg index 20 tool:run_toolkit

LLM waits over 5 minutes (Anthropic 5-minute cache TTL would expire between tool end and next request only if the TOOL gap exceeds it; LLM time itself is within one request):
- 20260929-213629-dd4aed: 2 waits >5 min, max 7.7 min
- 20261003-100404-9e6e55: 8 waits >5 min, max 12.8 min
- 20261003-110049-79b3d1: 2 waits >5 min, max 7.1 min

## What-if simulations (offline, token-count only)

### Mask old bulky tool results (keep first/last 350 chars) once they are older than 10 assistant steps and over 2000 chars

| chat | sum of requests | saved | % | results masked |
|---|---|---|---|---|
| 20260928-125940-5e681c | 5,144,876 | 2,082,290 | 40% | 33 |
| 20260929-213629-dd4aed | 16,949,898 | 4,917,979 | 29% | 19 |
| 20260930-201212-16f983 | 12,971,542 | 5,819,632 | 45% | 44 |
| 20261003-100404-9e6e55 | 32,002,808 | 4,663,891 | 15% | 19 |
| 20261003-110049-79b3d1 | 13,656,215 | 4,316,918 | 32% | 21 |

### Coalesce adjacent reasoning_details deltas (same type/format/index) before saving

- 20261003-100404-9e6e55: stored blocks 325,330 -> 150; JSON 24.7 MB -> 1.00 MB (4.1%)
- 20261003-110049-79b3d1: stored blocks 79,546 -> 87; JSON 6.3 MB -> 0.45 MB (7.3%)
