# Editor performance on big models

The build page is becoming an always-editable 3D workspace, so an edit has to feel instant on models of a thousand
bricks and more. This is what was slow, what changed, and how the answers were kept identical.

## Measured

Everything below is in-process on a warm toolkit (what the resident worker does), on `willowbank-cottage-v1.mpd`
(1,124 placements in 18 sections, copied read-only from the live data volume), one throwaway container, the worktree
copied onto the container's own volume. "Before" is master at 707e5796 run through the same script
(`run_editor`, the function behind every `ldraw-agent editor` call), "after" is this branch through `run_request`
(the resident worker's entry point). The host was shared with other test containers, so numbers carry about +-30%.
`add + snap` is a 3001 brick dropped on top of a part in the largest section (`hall-roof.ldr`, 314 parts), snapped
to it.

`willowbank-cottage-v1.mpd`, 1,124 placements in 18 sections (milliseconds unless stated; a range is the first and
the warm run of the same operation):

| operation | before | after |
|---|---|---|
| open: import the MPD, first build | 3,200 | 1,000 |
| open the same file again (import cache) | 3,200 | 28 |
| move (apply, rebuild the preview, commit) | 3,300 - 4,000 | 105 - 200 |
| undo | 3,600 - 4,300 | 25 - 32 |
| redo | about the undo | 28 - 34 |
| add, no snap | 3,800 | 125 - 140 |
| recolour / delete | not measured (same path as move) | 105 - 135 |
| add + snap | does not finish (earlier agent: the 600 s timeout) | 830 - 870 |
| full rebuild of the preview MPD, cold, whole-text validation | 6,200 | 900 |

Bigger models, same script:

| model | placements | open | move | undo | add | add + snap |
|---|---|---|---|---|---|---|
| house-v1 | 106 | 410 -> 130 | 430 -> 20 - 95 | 320 -> 6 | 610 -> 24 | 10,000 - 27,000 -> 110 - 310 (54-part section) |
| bradbury-building-los-angeles-v1 | 1,626 | 5,600 -> 1,700 | 5,400 - 6,300 -> 170 - 250 | 4,800 - 5,800 -> 40 - 50 | 6,600 -> 180 | not finished before -> 1,000 - 1,200 |
| the-tidal-observatory-final-v1 | 2,580 | -> 2,100 | -> 230 - 360 | -> 57 | -> 270 | -> 860 - 1,370 |

Through the real API (`apibench`: the FastAPI app, the resident worker, the cottage), steady state after the first
requests: move 178, undo 95, redo 110, view of the session 65, reopening the same build 60; the first open of a
session also starts the worker and loads the library (3,400). Moves and undos right after opening take 220 - 290
while the worker is still warming; an add + snap in the dense roof section takes 400 - 1,900 including its refusals.
Bradbury through the API: move 258, undo 250. Every operation also used to pay a toolkit process start and a library
load (seconds) that the worker now pays once; that is not in the "before" column.

Part-type warm-up (see below) took 10 s of idle time for the cottage's 42 part types, 9 s for Bradbury's 46, 6 s for
the observatory's 53; it runs between requests, not in them.


## What was slow

| cost | where | fix |
|---|---|---|
| Schema check re-read and re-compiled `plan.schema.json`, then validated every placement, five times per operation | `builder.check_schema` | validator built once; verdicts remembered by content (whole plan, then per placement), so an edit validates only what changed |
| Every placement was a fresh `Parts.part()` (file existence checks plus a header parse) in build and validation | `common.get_parts` | `CachingParts` keeps loaded `Part` objects |
| Whole-model validation (`validate_text`) after every edit, about two thirds of a rebuild | `editor.rebuild` | `incremental`: validate the lines that changed with the real validator, check the cross-line rules on the plan, whole text only when anything is unusual |
| Undo/redo and repeated states rebuilt from scratch | `editor.rebuild` | a validated plan seen before is answered from memory |
| Snap built the whole plan, inspected every placement of the section and every submodel, then checked up to 100 candidate poses against all of it, twice per batch (once for the undo, once for the plan) | `builder.snap_placement`, `connectivity.snap_report`, `editor._apply_snap` | see below; the second snap of a batch is answered from memory |
| One CLI process per operation (start-up and library load) | `routers/editor.py` | resident editor worker |
| Importing the MPD on every open | `commands/editor.py open` | import cache by content hash |
| First snap loaded the geometry of every part type it met | pyldraw `Parts.geometry` | warm-up between requests |

### Snap

* `snapindex.Neighbourhood`: world boxes of every placement (including leaves of submodel instances), from each
  part's bounds. Only what lies where the moving part could end up (around the fixed part, one part-size out) is put
  in the preview model; nested submodel instances are copied with only their nearby leaves. Anything of unknown
  size stays in. Cost follows local density.
* `fastbounds`: pyldraw moves every vertex of a part through a Python `position + matrix * point` to get one bounding
  box (about 30 ms per placement). One array product against the part's vertices gives the same box.
* `fastsnap`: the editor only asks for the connection nearest the drop. Candidates are generated and filtered exactly as
  `snap_report` does; they are examined in order of distance from the drop, each against the placements that overlap
  its own pose, with contact searches limited to the pairs that are read. When the first accepted candidate is
  collision free with a confirmed contact, nothing unexamined can outrank it, and the rest of its distance ties are
  examined. If that does not happen every candidate is examined, which is the full algorithm. Blocked candidates
  are only checked for being real connections when nothing usable exists (to choose between "collides" and "no
  candidate", as before). Submodel moving/fixed parts, Technic interfaces and incomplete geometry go to `snap_report`.
* `snap_report` itself (the CLI and the agents' `snap`) got the exactness-preserving parts: inspect once, local
  occupancy and local candidate checks, contacts only where they are read.

### Resident worker

`pipeline.py --serve` gets an `editor` op; `build_worker.get_editor_worker()` is a second warm process (a build can
run for minutes, edits must not queue behind it). The routes translate their CLI arguments into the request, so
the CLI stays the one source of truth and keeps working for agents; a busy, crashed or older worker falls back to
the CLI for that call, `LDRAW_NOVA_EDITOR_WORKER=0` turns the worker off. The reply carries the report without the plan
and the preview (they are on disk).

## How the answers stay the same

* Snapped placements: `tests/test_editor_performance.py` keeps the old algorithm (the whole plan built per snap,
  every candidate against the whole model, pyldraw's own bounds) and requires identical results, errors included, on
  24 random operations on synthetic parts and on random add-and-snap operations in two sections of the cottage.
  Interactive checks while developing: 10 + 14 random operations on the house model, 24 of 24 identical.
* Rebuild: the incremental path can only say "no errors" (never an error); for every plan that the whole validation
  rejects the tests require the same error text. Anything it does not understand (symbolic placements, `on`, `attach`,
  `repeat`, changed sections or headers, a possible duplicate within 0.005 LDU, colour 16 left at the top) validates the whole text.
* Cross-line checks on the plan are deliberately coarser than the originals, so they can only say "maybe".

## Tests

* Toolkit: `tests/test_editor_performance.py` (new: old-algorithm parity for snaps, schema memo, incremental rebuild
  against whole validation, bounds, import cache, worker entry point, idle jobs, warm-up jobs, snap memo),
  `tests/test_editor.py`, `tests/test_pipeline.py`, `tests/test_connectivity.py`, `tests/test_technic.py`: all pass
  (100 passed, 1 skipped for the editor, performance, connectivity and Technic files; 55 passed, 1 skipped for the
  editor and performance files after the last change).
* Backend (`test_editor_api.py` with new worker tests, `test_editor_builds.py`, `test_editor_agent.py`,
  `test_editor_palette.py`, `test_build_worker.py`) against this branch's toolkit: 38 pass; the two that fail
  (`test_save_is_the_next_version_in_my_builds`, `test_editing_a_chat_built_model_leaves_the_chats_editor_alone`) fail
  the same way on master in the throwaway container, where LeoCAD cannot render and the gallery then tries to start
  a task outside an event loop.

## Left

* A snap whose best candidate has review warnings or an unconfirmed contact examines all (up to 100) candidates;
  those cost about 10 - 50 ms each, more in a dense area. A refused snap ("collides", "no candidate") costs
  0.2 - 1.9 s on the cottage roof and the observatory.
* The move/undo round trip through the API is about 270 ms on the cottage in steady state and 500 ms on Bradbury
  with a loaded host; the budget of 300 ms holds for a model of the cottage's size on an idle machine.
* The first request of a session starts the editor worker and loads the library (seconds); it is not started with the
  app.
* The first snap after a model opens still loads geometry for part types the plan does not use (the part being added).
* `Parts.geometry` itself is slow on first load (resolving files and connection metadata); only the warm-up hides it.

## Adding a brick: latency (add-latency branch)

What the owner saw: after a click the brick appeared only when the whole round trip finished (request, edit, rebuild,
the full plan and preview MPD back, parse, draw), plus a second round trip when the snap was refused.

Now the canvas draws the brick at the drop position in the same frame as the click (pending outline), sends the
request in the background (adds queue in order), and the answer is the changed entries only; the brick slides onto
its snap in 120 ms, or is removed with the reason when the add is refused. The editor worker is warmed when a workspace
opens, and a snap that has examined candidates for 0.25 s without finding a usable one is refused.

Through the API (FastAPI app, resident worker warmed, in-process `TestClient`, median of 6, host shared so +-30%):

| model, operation | before (whole view) | after (delta) |
|---|---|---|
| brick visible after click | after the full round trip (add 160 - 250 ms server + transfer + parse; with a refused snap 560+ ms) | same frame (optimistic), < 50 ms |
| cottage (1,124 parts) add, no snap: server confirm | 160 ms (max 400) | 112 ms (max 137) |
| cottage add, no snap: response size | 172 KB | 5.3 KB |
| cottage undo: server / size | 64 ms / 173 KB | 37 ms / 5.2 KB |
| house (106 parts) add, no snap: server / size | 38 ms / 18.4 KB | 38 ms / 1.2 KB |
| house undo: server / size | 21 ms / 19.5 KB | 16 ms / 1.2 KB |
| cottage add + snap in the dense roof section (5 of 6 drops were refused and retried unsnapped) | 568 ms | 561 ms (hidden behind the optimistic brick) |
| house add + snap (4 of 6 refused, retried) | 248 ms | 251 ms |
| first snap of a session, cold geometry (in-process, cottage) | 1,800 ms | hidden by the warm-up on open |

Not changed: the server still rebuilds and validates the preview (about 90 of the 112 ms on the cottage) before
answering, because the commit writes plan, preview and history together and a failed validation must refuse the
edit. A refused snap on the cottage costs about 130 ms warm here, so the 0.25 s budget only matters in denser
places; the unit test shows the number of examined poses dropping when it runs out.

