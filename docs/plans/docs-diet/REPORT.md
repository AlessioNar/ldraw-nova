# Docs diet: what can be cut from the agent system prompt

Analysis only. No quality measurement was possible (no LLM API key). Branch `swarm/docs-diet`, based on master at the time of writing. Line numbers refer to the files before the patch. Token figures are **chars/4** estimates, not tokenizer output; real tokenization of markdown plus shell snippets is probably 10-15% higher, which scales every figure below but not the percentages.

## 1. Measurements

Stable prefix, assembled by `app/web/backend/system_prompt.py` (template, then `instructions.md`, then the five `BUILDER_GUIDES` of `toolkit.py`). The work-folder listing and `NOTES.md` come after the cache breakpoint and are not counted.

| Component | Bytes | ~Tokens | Share |
|---|---:|---:|---:|
| `app/web/backend/prompts/system.md` | 6,128 | 1,532 | 7.9% |
| `instructions.md` | 23,463 | 5,866 | 30.3% |
| `docs/agent/ldraw-reference.md` | 6,884 | 1,721 | 8.9% |
| `docs/agent/geometry.md` | 8,030 | 2,008 | 10.4% |
| `docs/agent/visual-design.md` | 11,602 | 2,901 | 15.0% |
| `docs/agent/reference-discovery.md` | 12,872 | 3,218 | 16.6% |
| `docs/agent/validation.md` | 8,562 | 2,141 | 11.0% |
| **Prefix total** | **77,541** | **19,385** | 100% |

(The brief said ~70 KB; it is 77.5 KB today. The tool schemas in `tools.py`, about 3 KB, are sent in addition.)

On demand (read via `read_file`, then carried in the history): the other 11 files in `docs/agent/*.md` total 151,708 B (~37.9k tokens), largest `tooling.md` 25.5 KB, `vehicles.md` 15.6 KB, `complex-models.md` 12.4 KB, plus `docs/agent/technic-patterns/*.md` (~26 KB).

## 2. Findings

### 2.1 Stale or dead references: none found

* Every `ldraw-agent ...` command line in `instructions.md`, `system.md` and all `docs/agent/*.md` (about 210 lines, backslash continuations joined) was parsed against the real argparse tree (`ldraw_tools.cli.parser()`, the code behind `ldraw-agent --help`). Zero unknown subcommands, zero unknown flags (the checker was verified to flag a deliberately bad line). Every other `--flag` in the text belongs to an external tool (`jev-rerank`, `mpd2glb.sh`, `ldraw-render-steps.sh`, `uv`, `git diff --check`).
* Choice values spot-checked: `examples --family {building,vehicle,reference,technic,mechanism,spaceship}`, `spaceship brief {starfighter,freighter,capital-ship}`, `discover search --engine {jev,fts} --construction {system,all,technic-structure,mechanism}`. Named recipes and palettes (`grand-tourer`, `pickup`, `desert-utility`, `driver-cockpit`, `arched-window`, `rose-townhouse`, `botanical-bookshop`, `arcade-bay`, `street-lantern`, `frame-tower`, `striped-awning`, `flower-planter`, `book-sign`, `b-wing`, `touring`, `classic`) exist in `ldraw_tools/`; `worm-drive` exists in `examples/mechanism-atlas/catalog.json`.
* Every markdown link and backticked `docs/ examples/ data/ ldraw_tools/` path in those files resolves. `data/ldraw-info.db` contains both `PARTS_DESCRIPTIONS` and `PARTS_DESCRIPTIONS_JEV`, so both Jev invocation styles (C3) are valid.
* Tool names used in `system.md` (`run_toolkit`, `run_shell`, `run_python`, `read_file`, `write_file`, `view_image`, `publish_model`, `report_progress`) all exist in `tools.py`. `system.md` never uses `{ldraw_dir}` although `system_prompt.py` substitutes it (harmless).
* Caveat for the "restates `--help`" class: `ldraw-agent --help` and the subcommand helps list flag names with almost no descriptions. Prose explaining flag semantics is therefore not redundant with `--help`; the real redundancy is with the tool schemas (`tools.py`) and between the docs themselves.

### 2.2 Contradictions and friction

* **C1 (highest impact). "Read these" versus "already loaded".** `instructions.md:17-21,28,37,39` tell the agent to Read the LDraw rules, Geometry, Visual design, Validation and Reference discovery guides. `system.md:6` and `:12` say those five guides are already supplied and need no tool reread. A model that obeys the instructions re-reads ~48 KB (~12k tokens), which then rides in the history for the rest of the chat. Fix (NEEDS BENCHMARK, touches the standalone doc): mark the five guides "(preloaded in the web app)" in `instructions.md`. The patch only fixes `system.md:3`, which said "read before planning or using tools" for text that is already inline.
* **C2.** `instructions.md:15` "Run `./setup.sh` if needed" versus `system.md:17` "dependencies are already installed; do not run setup". Not patched (standalone doc needs it).
* **C3. Two recipes for the same Jev step.** `instructions.md:62-71` and `tooling.md:116-152` use `jev-rerank --table-field parts_descriptions.description --top 10`; `reference-discovery.md:5-42` uses `discover search` and `PARTS_DESCRIPTIONS_JEV.full_description`, model `jev-1.13.0`. `tooling.md:114` itself says the direct form's key instructions do not apply to the new views. Both work; consolidating on `discover search parts` is NEEDS BENCHMARK.
* **C4.** `system.md:19` says commands "may run up to 30 minutes"; the tool schemas default to 300 s (`run_toolkit`) and 60 s (`run_shell`, `run_python`), maximum 1800. Nothing tells the agent to pass `timeout` for long renders. Worth one clause, not a size issue.
* **C5.** "Analytical mechanism verification is deferred (at the user's request)" appears 15 times in 13 files (`instructions.md:25`, `validation.md:3`, `visual-design.md:5`, `reference-discovery.md:54`, `tooling.md:29,52`, `technic.md:3`, `mechanisms.md:3,107`, `complex-models.md:3`, `build-manuals.md:49`, `spaceships.md:46`, `technic-design.md:15`, `vehicles.md:5`, `verification.md:7`). Project-history phrasing; the CLI help already says "no motion analysis". The patch removes the `visual-design.md` copy only (inside a larger duplicate paragraph).

### 2.3 Duplicated passages

Exact sentence or command duplicates found by script: `instructions.md:77` = `tooling.md:227` = `visual-design.md:41` (catalog arch command); `instructions.md:150` = `tooling.md:201`; `instructions.md:25` = `mechanisms.md:3`; `technic.md:3` = `verification.md:7`; `tooling.md:59` = `vehicles.md:76`; `tooling.md:230` = `visual-design.md:45`; `tooling.md:232` = `visual-design.md:79`; `complex-models.md:8` = `tooling.md:157`. The bulk of the redundancy is paraphrase, by topic:

| Topic | Copies (file:line, pre-patch numbering) |
|---|---|
| Contacts: auto up to 500 placements, `coverage.contacts_skipped`, `--contacts all|none` | `instructions.md:157`, `geometry.md:73`, `validation.md:22`, `tooling.md:205`, `complex-models.md:119-121` |
| Stacking rule `y_upper = y_lower - upper_body_height`, 20/24/8 LDU, half-stud parity | `instructions.md:118-120`, `geometry.md:11-36` |
| Plan fields `on`, `offset_studs`, `yaw`, `attach`, `repeat`, `snap` | `instructions.md:105-114`, `tooling.md:173-185`, `geometry.md:34`, `complex-models.md:83,98`, `snapping.md` |
| Colour 16/24, rigid rotations, no mirror/shear | `instructions.md:121-122`, `ldraw-reference.md:35-37,43-44`, `validation.md:36-37`, `tooling.md:185,187` |
| Jev availability check and FTS fallback | `instructions.md:37,62`, `system.md:13,20`, `reference-discovery.md:5-26,42`, `tooling.md:112,152`, `build-manuals.md:21` |
| Visual review checklist ("open the images; silhouette, palette, depth; do not claim review unopened") | `instructions.md:163,165`, `visual-design.md:96-109`, `system.md:14`, `geometry.md:59` |
| Technic / vehicle / spaceship routing and design rules | `instructions.md:22-27,45-60,139`, `visual-design.md:5-15`, `tooling.md:40-83`, plus the dedicated guides |
| Catalog / design / part-board command blocks | `instructions.md:75-85`, `visual-design.md:39-46,75-83`, `tooling.md:225-234` |
| `prepare-glb.sh` / `mpd2glb.sh` | `instructions.md:15`, `geometry.md:61-67`, `tooling.md:213` |
| `ldraw-render-steps.sh` construction review | `instructions.md:167`, `geometry.md:59`, `tooling.md:215`, `build-manuals.md` |
| Exit codes 0/1/2 | `instructions.md:159`, `tooling.md:219` |
| `extract` / `study` behaviour | `instructions.md:97-99`, `tooling.md:161-163`, `complex-models.md:41,54` |
| "Do not present copied source as your own design" | `instructions.md:99`, `system.md:11` |
| "Foundation guides already supplied" | `system.md:6` and `system.md:12` |

### 2.4 Restating tool schemas

`system.md:19,21,22,23,27` (and `:17-18` against `instructions.md:33`) re-describe what `run_toolkit`, `run_shell`, `run_python`, `view_image`, `write_file`, `publish_model` and `report_progress` already say in `tools.py` (output goes to `output/`, shared files read-only, view before claiming review, publish the final model, report progress). Parts not in the schemas (`card_url` vs `viewer_url` vs `model_url`, uploads under `output/uploads/`, the `NOTES.md` resume protocol, artifact URL rule) must stay.

### 2.5 Historical narrative and anecdotes

* `docs/agent/verification.md` (10,261 B) is a pure changelog: dates, SHA-256s, "211 passing tests", macOS and Python versions. It is not in the prefix and `instructions.md` does not reference it; only `examples/modular-street/README.md:22` links it. Recommendation: move it to `docs/reports/` and fix its relative links. A `git mv` was refused by the sandbox in this run, so it is **not** in the patch.
* `tooling.md:207` ("now runs ... no longer uses LDView"), `tooling.md:221` (what pytest covers), `geometry.md:7` (the `3659` arch 37.41191 vs 28 anecdote, also in `verification.md:33`), `visual-design.md:60` ("the updated example chose layered leaves"), `visual-design.md:94` (Copper Lane dormer and 168 LDU, already in `examples/modular-street/README.md:20` and `complex-models.md:71`), `validation.md:47-49` ("Large-reference regressions"), `visual-design.md:110-111` (LeoCAD render flags).
* Kept on purpose: the `complex-models.md` Bookshop sections (on demand, a deliberate case study).

## 3. Classification

### SAFE (pure duplication, anecdote, dead statement): implemented in the patch

| # | Cut | Where | ~Bytes |
|---|---|---|---:|
| S1 | Technic/vehicle/spaceship routing paragraphs (dup of `instructions.md` sections 1-2) | `visual-design.md:5-15` | 1,646 |
| S2 | Catalog/part command block (dup of `instructions.md:75-85`) | `visual-design.md:39-46` | 353 |
| S3 | `design`/`build`/`inspect`/`render` block reduced to its one unique line | `visual-design.md:75-83` | 303 |
| S4 | Anecdote sentences (layered leaves; Copper Lane dormer, 168 LDU) | `visual-design.md:60,94` | 322 |
| S5 | LeoCAD render flags and "keep changes in plans" (dup) | `visual-design.md:110-111` | 290 |
| S6 | "Module interfaces and large scenes" section (dup of `instructions.md:113,155,157`, `complex-models.md`) | `geometry.md:69-73` | ~1,000 |
| S7 | GLB export block (dup of `instructions.md:15`, `tooling.md:213`) | `geometry.md:61-67` | ~460 |
| S8 | `3659` anecdote; "no 4-LDU gap" and half-stud example (dup of `instructions.md:119-120`); `on` paragraph reduced to its unique restrictions; `ldraw-render-steps` sentence | `geometry.md:7,32,34,36,59` | ~790 |
| S9 | "Large-reference regressions" narrative (one unique sentence kept) | `validation.md:47-49` | 621 |
| S10 | Duplicate sentences: copied-source warning, LeoCAD adapter note, render-adapter clause | `instructions.md:15,99,165` | 221 |
| S11 | "read before planning or using tools" (see C1) and the second "guides already supplied" | `system.md:3,12` | 79 |
| S12 | Changelog wording and test-coverage paragraph (on demand) | `tooling.md:207,221` | 503 |

### PROBABLY SAFE (restates tool schemas or on-demand docs; not patched)

| # | Candidate | ~Bytes |
|---|---|---:|
| P1 | `system.md:19,21,22,23,27` tool-schema restatements (keep the non-schema clauses) | 1,734 |
| P2 | `system.md:17-18` (dup of `instructions.md:33` and the write_file schema) | 708 |
| P3 | `reference-discovery.md:44,46,48,76-82,84,88`: index internals, result-card file listing, cache/resume behaviour (no agent action depends on them) | 2,786 |
| P4 | `validation.md:3,7`: mechanism and Technic paragraphs that `mechanisms.md` and `technic.md` already carry (the instructions force reading those) | 1,061 |
| P5 | `geometry.md:42-47` connector/snap recipe (full version is `snapping.md`, linked from `instructions.md:111`) | 1,014 |
| P6 | Collapse the other "analytical verification is deferred" mentions (C5) | small, mostly on demand |

P1-P5 total 7,303 B, about 1,826 tokens.

### NEEDS BENCHMARK (behavioural guidance; removal can change outputs)

| # | Candidate | ~Bytes |
|---|---|---:|
| B1 | Keep one home for the placement rules: `instructions.md:116-124` or `geometry.md:24-36` (they overlap; drop the other) | 1,187 or 1,307 |
| B2 | Replace the direct `jev-rerank` recipe `instructions.md:62-71` by `discover search parts` (C3) | 930 |
| B3 | Keep one visual-review checklist among `instructions.md:163,165`, `visual-design.md:96-109`, `system.md:14` | 331-1,320 |
| B4 | Shorten `instructions.md:45-60` category paragraphs and vehicle command list to pointers into `vehicles.md`, `spaceships.md`, `technic-design.md` | 3,033 |
| B5 | `ldraw-reference.md` record-type table and MPD example (`:11-22`, `:48-71`); the builder writes this itself | 2,577 |
| B6 | `validation.md:9-25` check-coverage table (explains diagnostics; useful when interpreting reports, but verbose) | 3,755 |
| B7 | C1 fix: mark preloaded guides in `instructions.md` (net bytes about zero; may save ~12k history tokens) | ~0 |

B1/B3 are alternatives, so the realistic total after overlaps is 9-11 KB (2.3-2.8k tokens).

## 4. Saving from the SAFE patch

`docs/plans/docs-diet/safe-cuts.patch` is generated with `git diff` against the unmodified tree, checked with `git apply --check`, and **not applied**. Apply with `git apply docs/plans/docs-diet/safe-cuts.patch`.

| File | Before | After | Saved |
|---|---:|---:|---:|
| `system.md` | 6,128 | 6,049 | 79 |
| `instructions.md` | 23,463 | 23,242 | 221 |
| `geometry.md` | 8,030 | 5,777 | 2,253 |
| `visual-design.md` | 11,602 | 8,688 | 2,914 |
| `validation.md` | 8,562 | 7,941 | 621 |
| **Prefix** | 77,541 | 71,453 | **6,088 B = ~1,522 tokens (7.9%)** |
| `tooling.md` (on demand) | 25,533 | 25,030 | 503 B (~126 tokens, only when read) |

Adding P1-P5 reaches about 13.4 KB (~3.3k tokens, 17%); adding a conservative share of B reaches about 23 KB (~5.8k tokens, 30%).

### Cost over a 150-step chat

The prefix sits behind a cache breakpoint. Assuming Anthropic-style pricing (cache write 1.25x base input, cache read 0.1x), one write plus 149 reads is 1.25 + 14.9 = **16.15 base-input-token equivalents per prefix token per chat, not 150**. So:

* SAFE patch: 1,522 x 16.15 = ~24.6k base-input-token equivalents per chat (about $0.07 at an assumed $3/MTok base input). The whole prefix costs ~313k equivalents (~$0.94) per chat, so the patch removes 7.9% of that line, a small part of the total chat cost (output tokens and the growing history dominate).
* Providers without prompt caching (`system_message()` falls back to a plain string for OpenAI, Ollama and others): 1,522 x 150 = 228k input tokens per chat, about ten times more.
* Cache misses raise it: the TTL is minutes while a step may run a 30-minute command; each expiry re-bills the prefix at the 1.25x write rate. Worst case (a write every step) the factor is 187 instead of 16.
* Not in these numbers: fewer contradictions, less to reconcile. The largest practical lever is C1: one avoided re-read of the guides is ~12k tokens carried in the history, roughly eight times the whole SAFE patch.

## 5. Offline benchmark design (needs an API key; for the person who runs it)

Goal: show that PROBABLY SAFE and NEEDS BENCHMARK cuts do not change tool use, constraint compliance or output quality. Build each variant with the real `system_prompt_parts()`: V0 master, V1 SAFE patch, V2 = V1 + P1-P5, V3 = V2 + B1/B2/B4, V4 = V3 + B5/B6. Same model, temperature 0, 3 seeds per cell, the real tool server (`tools.py`) with the toolkit installed (`LDRAW_DIR` set), `max_steps` 60.

Prompts (12) and their checks:

1. "Build a small red sports car." Design brief written before the first plan (order of write_file calls); `vehicle plan` or `examples --family vehicle` used; final MPD passes `validate --geometry` and `vehicle check`; wheel centres on the road; no claim that `physical_validity` is proven.
2. "Build a 2x4 brick with a 1x1 plate centred on top." Exact stacking (`y_upper = y_lower - 8`), correct half-stud offsets, no duplicate placements. Tests B1.
3. "Make a medieval gatehouse with an arched entrance." `catalog` or `part-board` used; `@category` refs resolve; zero colour 16/24 misuse; module checklist in NOTES.md.
4. "Build an X-wing style starfighter." `spaceship brief` used; spine, cockpit and engine parts in the BOM; no road-wheel checks run.
5. "A Technic frame tower with pin-connected braces." `technic plan` and `technic check` exit 0; no `vehicle` commands.
6. "A simple gearbox with a worm drive." Mechanism workflow, `--contacts none` as instructed, no analytical-motion claims.
7. "Recolour `examples/bridge.plan.json` to blue and tan." Works on a copy under `output/`, source untouched, rebuild succeeds, not presented as a new design.
8. "Repair this MPD" (upload with a mirrored brick, a colour-24 type-1 line, a duplicate placement). `assembly.nonrigid`, `colour.*`, `assembly.duplicate` fixed rather than suppressed; result validates. Tests B6.
9. Prompt 1 with `TYPESAFE_API_KEY` unset. One availability probe, then FTS fallback labelled offline, no retry loop. Tests B2/P3.
10. "Add a bookshop sign and awning to this street" with an uploaded MPD of more than 500 placements. Uses `--section`, reports `contacts_skipped` honestly, uses `design details`. Tests the contacts-rule copies.
11. A resume turn with a populated `NOTES.md` and "continue". Reads NOTES.md, does not restart, and does not `read_file` the five preloaded guides (C1 metric).
12. A chat-only question ("what is colour 16?"). Answers from the prompt without tool calls.

Metrics per (variant, prompt, seed): (a) the hard checks above as pass/fail; (b) steps, tool calls, `read_file` calls on preloaded guides, input tokens and cache write/read tokens from API usage; (c) validator error and warning counts on the final MPD; (d) blind pairwise judgment (human or LLM, order randomized) of final renders against V0 for silhouette, palette and detail, since checks do not capture design quality; (e) rate of "claimed visual review without `view_image`" and "claimed buildable". Acceptance: no hard-check regression on any prompt; step count and tool-error rate not worse than V0 beyond the seed spread; pairwise win-rate 45-55% on visual quality. Reject any variant that raises guide re-reads or unopened-image review claims. Report cost per chat from measured cache tokens, not from the estimates above.
