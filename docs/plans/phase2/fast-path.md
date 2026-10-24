# Fast-path decision API (phase 2, part A)

The decision engine the build page calls. It reads a request (rules first, else ONE cheap structured call), decides how to
serve it and returns what the UI needs. It builds nothing and creates no build: to build, call the existing build endpoint
(`POST /api/chats/{id}/build`) or the generator with the returned `generator` and `params`. The paid AI build is never started
automatically: `route: "needs_ai"` carries an estimate so the UI can ask first.

Code: `app/web/backend/fast_path.py` (engine), `routers/describe_api.py` (HTTP), `generator_bridge.py` (generator registry as data).

## Settings: understanding model

`PUT /api/llm-understanding/default` with `{"id": "<llm entry id>" | null}` -> `{"understanding_id": ...}` (400 for an unknown id).
`GET /api/llm-models` also returns `understanding_id`. `null` means automatic: a configured Claude Haiku 5.5 entry, else the main
model. Order used by `understand.model_params()`: explicit `llm_id`, env `LDRAW_NOVA_UNDERSTAND_LLM_ID`, the Settings choice, Haiku 5.5, main model.
Deleting the chosen model resets it to automatic. The Settings page has the picker under Roles.

## POST /api/describe

Request:

```json
{ "text": "make the roof blue",
  "build_id": "optional, echoed back",
  "previous_text": "optional: the last message, so 'again' is answered by a rule",
  "current": { "generator": "house", "params": { "width": 8, "roof_colour": "dark red" } } }
```

`current` is the build's generator and its current params (omit for a new request). 422 for empty text or an unknown `current.generator`.

Every response also has `understanding_cost_eur` (what this call spent; `0.0` when a rule answered, `null` if the model is unpriced),
`understood_by` (`rules` | `llm` | `rules_fallback` when no model answered), `understanding_model`, and `build_id` when sent.

### route "generator": build with a generator

```json
{ "route": "generator", "generator": "grand-tourer", "title": "...", "params": { "length": 18, "wheelbase": 10, "palette": "heritage-racing", "body": "red", "wheels": "black" },
  "notes": [], "confidence": 1.0, "cost_eur": 0.0, "seconds_estimate": 20,
  "understanding_cost_eur": 0.0, "understood_by": "rules", "understanding_model": null }
```

`params` is complete (defaults filled) and valid against the generator's schema. Out-of-range values are clamped and listed in `notes`
(`"width 40 adjusted to 24 (6-24, even)"`), as are assumptions (`"read the size as studs (not bricks)"`) and requests the design cannot
honour (`"chimney is not supported by the grand-tourer design; left out"`). `cost_eur` is the build's cost (always 0); `seconds_estimate`
is for the resident worker.

### route "edit": change the current build's params

```json
{ "route": "edit", "generator": "house", "params": { "...": "all params after the change" },
  "changes": [ { "param": "roof_colour", "label": "roof colour", "from": "dark red", "to": "blue" } ],
  "notes": [], "confidence": 1.0, "cost_eur": 0.0, "seconds_estimate": 15, "understanding_cost_eur": 0.0 }
```

Only changes that map to generator params ("make the roof blue", "4 studs longer", "add a chimney", "two more windows"). Parts of a message
that do not map are listed in `notes`; a message with nothing mappable becomes `needs_ai`. `undo` / `revert` return `changes: []` and
`"history": "undo" | "revert"` (the build page applies its version history). "Make it a truck instead" returns a `generator` route for the
new design with the colours carried over.

### route "needs_ai": ask the person before the paid build

```json
{ "route": "needs_ai", "reason": "no generator builds a helicopter",
  "spec": { "intent": "new_model", "subject": { "family": "aircraft", "kind": "helicopter" } },
  "estimate": { "eur_low": 1.0, "eur_high": 2.0, "minutes_low": 15, "minutes_high": 30, "basis": "default" },
  "understanding_cost_eur": 0.0004 }
```

`estimate` is the quartile range (p25-p75, plus `eur_median` / `minutes_median`) of the latest turn's cost and duration over finished
chats (the same population as the composer's step estimate), `basis` says so ("quartiles of 12 finished chats"); with fewer than 3
finished chats it is the default 1-2 EUR, 15-30 min. `spec` is the understood spec.v1, to hand to the AI build.
Reasons include: no generator for the subject or kind, a named real set or franchise, "only parts I own", a required feature the generators lack.

### route "clarify": too vague

```json
{ "route": "clarify", "question": "What would you like to build? For example \"a small red car\" or \"a cottage 10 studs wide\".", "understanding_cost_eur": 0.0004 }
```

Also returned for questions and chit-chat, for an edit with no current build, and for an edit that changes nothing.

## GET /api/describe/estimate?text=...[&generator=house]

Rules only: no model call, no toolkit call, a few milliseconds (the response has `took_ms`; tests bound it under 200 ms). Safe to call
debounced while typing. `generator` (optional) lets edit phrases like "make the roof blue" be recognised.

```json
{ "route": "generator", "generator": "pickup", "confidence": 0.6, "decided_by": "rules", "cost_eur": 0.0, "seconds_estimate": 20,
  "hint": "Fast path: free, ~20 s", "took_ms": 1.2 }
{ "route": "needs_ai", "confidence": 0.3, "decided_by": "rules", "estimate": { "eur_low": 1.0, "eur_high": 2.0, "minutes_low": 15, "minutes_high": 30 },
  "hint": "AI build: ~€1–2, ~15–30 min", "took_ms": 0.8 }
```

Same route names as `/api/describe`. `confidence` is 1.0 only for exact patterns (the home-page suggestions, "make the roof blue",
"undo"); keyword readings are at most 0.8, so show the hint softly below about 0.7. `hint` is empty for `clarify` and for text under 3
characters. The real `/api/describe` may decide differently once the model has read the text.

## Notes for the build page

- Call `/api/describe` on submit only; call the estimate while typing.
- `generator` / `edit` routes: run the generator through the build pipeline with `params`; the same `(generator, params)` is a valid cache key.
- `needs_ai`: show `reason` and `estimate`, let the person confirm, then start the AI build with `spec`. No automatic fallback.
- The generator registry is dumped once from the toolkit interpreter (about 2 s) and cached in `CONFIG_DIR/generators.json`; the first
  `/api/describe` after a toolkit update pays that. Until it is loaded the estimate endpoint falls back to a built-in family list.
