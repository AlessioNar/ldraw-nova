# End-to-end cost and quality check (brief 09): results template

Status: NOT RUN. Fill the tables from `e2e-out/results.jsonl` (`e2e.py report --results ...`). The owner capped the whole check at **3 EUR in total**, so there is one swarm run, compared with two single-Sonnet runs that were measured earlier on the same prompt. No new baseline is run.

Run: Sonnet 5.5 planner (`anthropic/claude-sonnet-5-5`) + Haiku 5.5 workers (`anthropic/claude-haiku-5-5`), prompt "small" (the spaceship), `budget_eur` 2.5, permissions full, total cap 3.00 EUR. Date: ____. Commit: ____.

Prompt: "Build a small spaceship: a white hull with blue accents, two swept-back wings, a clear cockpit canopy at the front and twin engines at the back."

Other configurations (single Opus, Opus planner + Haiku workers) and the medium and hard prompts exist behind `--configs` / `--prompts` and need the owner's approval for the extra spend.

## 1. Measured cost, swarm run against the two single-Sonnet baselines

| | single Sonnet run 1 (measured) | single Sonnet run 2 (measured) | Sonnet + Haiku workers (this check) |
|---|---|---|---|
| wall time | 25.0 min | 24.0 min | |
| model seconds / tool seconds | not split | not split | |
| steps | 23 | 29 | |
| output tokens | 100k | about 145k | |
| EUR total | 1.51 | 2.05 | |
| EUR planner / workers | all planner | all planner | |
| delegations, statuses | none | none | |
| repair rounds | n/a | n/a | |
| ended how | cut off while publishing | stopped by the 2 EUR budget | |
| model published | no (cut off) | no | |
| validate verdict | n/a | n/a | |
| parts check | n/a | n/a | |

(The 145k output figure for run 2 is the one quoted in the brief's hand-off; confirm against the recorded chat. The README table estimate for this configuration is 0.94 EUR; for single Sonnet 5.0 EUR for a finished model, which the two runs did not reach.)

Is the 2 EUR target met for this prompt? ____ . At what quality? ____ . Note that the brief's medium prompt is NOT run under the 3 EUR cap, so the medium-model claim stays unmeasured.

## 2. README section 8 against the measurement

List every assumption that was off by more than 30 percent.

| assumption (README section 8) | assumed | measured | off by | note |
|---|---|---|---|---|
| planner steps | 15 | | | |
| planner output tokens | 30k | | | |
| planner cost | $0.79 (Sonnet) | | | |
| workers per run | 6 | | | |
| worker steps each | 20 | | | |
| worker cost each (case A) | $0.029 | | | |
| repair and retry overhead | +30% | | | |
| total | 0.94 EUR | | | |

## 3. Quality read (owner)

Renders are copied to `e2e-out/small-sonnet+haiku-1/` (published model and `publication/*/home.png`). The baseline runs published nothing, so there is no side-by-side against them; compare with any earlier finished spaceship if one exists.

| criterion (1 to 5) | score | comment |
|---|---|---|
| recognisable as the prompt | | |
| stable (connected, no floating parts) | | |
| owned parts only (parts check) | | |

## 4. Defaults from the data

One run is a sample, not a distribution (the same Sonnet prompt took 100k and 145k output tokens on two runs). State what the run does and does not support before changing any default.

| default (settings.py) | current | conclusion | reason |
|---|---|---|---|
| worker count | | | |
| step caps | | | |
| `max_eur` per worker | | | |
| repair rounds | | | |
| builders delegated at all | | | |

Follow-ups for a new brief: ____
