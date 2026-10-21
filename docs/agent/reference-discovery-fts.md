# Learn from models, submodels and their actual parts (keyword search)

This is the discovery guide for installations without a TypeSafe API key, so `discover search` uses local FTS: **keyword (BM25) ranking, not Jev semantic ranking**. It works well for distinctive words and ranks less well for descriptions of purpose or appearance. The complete guide, `docs/agent/reference-discovery.md`, describes the optional Jev engine and holds the full catalog, review and export reference; read it only if you need a detail missing here.

Use whole models for proportions, palettes and a module checklist; use submodels for construction ideas and details. Compare source images and measured contents before choosing a construction. The [reference atlas](../../examples/reference-atlas/README.md) contains a 220-reference catalog manifest, curated examples and selected parameterized recipes.

## Searching

```sh
./ldraw-agent discover index
./ldraw-agent discover search submodels 'street lamp' --max-parts 150 --limit 8 --report output/lamps.json
./ldraw-agent discover search parts 'jet engine cylinder housing' --limit 6 --report output/engine-parts.json
./ldraw-agent discover parts output/lamps.json --limit 12
./ldraw-agent examples --family reference --limit 8
```

`discover search` defaults to `--engine auto`, which uses FTS here. There is nothing to check or configure: do not probe Jev, ask for a key or retry. Each result states `engine_requested`, `engine_used` (`fts`), `ranking` and, when Jev was expected but unusable, a short `fallback_reason`. Mention a fallback once, briefly. Always report these results as keyword/BM25 ranking, not Jev semantic ranking. A `--engine jev` request fails with an error here.

Write keywords for BM25:

- Use a few distinctive nouns and part or construction words that a description would contain (`lamp post globe`, `wheel arch`, `hinge plate`). Tokens are ORed, stop words are dropped, and results with more or rarer matching terms rank higher.
- BM25 matches wording, not meaning. Run several queries for one role with synonyms (`wheel`, `tyre`, `rim`), more specific and more general terms, and merge the candidates by judgement.
- Queries need a searchable term; punctuation is ignored. Long natural-language sentences add noise, so prefer 2 to 5 keywords.
- `--yes`/`--no` criteria are Jev features and do not improve FTS ranking; narrow with keywords and filters instead.
- `--pool` sets how many ranked results are filtered and diversified into `--limit`; `--candidates` applies to Jev only. Do not fill the list with poor matches. Returned `stats`, rejections and `fts_rank` values show what was filtered.

Search `parts`, `models` or `submodels` for the current role. The source is the three `*_DESCRIPTIONS_JEV.full_description` fields in `data/ldraw-info.db` with annotated sources in `data/models-annotated/`; a local index corrects descriptions against source headers and classifies records. The supplied database and sources stay untouched. Only the parts library is configured externally: `--library`, then `LDRAW_DIR`, then `LDRAWDIR`. Each result has a stable `id`, exact `model`/`section` or `part`, indexed and corrected text, source hash, parent description and rank.

## Filters

The defaults hide embedded part definitions, empty or raw-geometry-only sections, internal/legacy part candidates and identifiable incompatible product families. Submodel and model candidates are inventoried to reject nonphysical accounting, apply part-count and Technic-share limits, group matching shapes (ignoring colour and global translation, not mirrored or rotated variants) and cap variants from one parent.

Use `--min-parts`, `--max-parts`, `--max-technic-share` (default 0.5, a title-prefix content filter, not a taxonomy; 0 requests none), `--parent-cap` and `--all-families` deliberately. Source names do not establish minifigure fit or scale. For Technic work use `--construction technic-structure` ([Technic structures](technic.md)) or, for the authorized stage 2, `--construction mechanism` (then `mechanism prepare FILE --section NAME --outdir DIR` and the [mechanism workflow](mechanisms.md)); `--construction system` keeps System defaults and `all` exposes all families. Results still need parent-context, joint-role, restraint and insertion review. A rank is not an import approval.

## Study, compare, review

Follow the [build-manual workflow](build-manuals.md) to turn a returned model/section into pages with highlighted additions. Then render a short list and open the images:

```sh
./ldraw-agent discover catalog output/lamps.json --outdir output/lamp-references --jobs 2
./ldraw-agent discover show submodel-2bd03fab63ff8deee3a007d0
./ldraw-agent discover prepare submodel-2bd03fab63ff8deee3a007d0 --outdir output/lamp-references --contacts all --views home front back right left top bottom
./ldraw-agent discover review submodel-2bd03fab63ff8deee3a007d0 --catalog output/lamp-references \
  --decision adapt --viewed-views home front right top --note 'Why this fits and what to change.'
./ldraw-agent discover example submodel-2bd03fab63ff8deee3a007d0 --catalog output/lamp-references \
  --output output/my-lamp-example --title 'Globe street lamp' --scale minifigure \
  --lesson 'What the construction teaches.' --placement-notes 'Orientation, envelope and base to preserve.'
```

Open the catalog's `index.html` and compare the listed dimensions and parent transforms; images are framed independently and keep source-local orientation. Each card holds `source.mpd` (dependency-closed, attributed), `preview.mpd`, `extraction.json`, `card.json` (BOM, bounds, dependencies, diagnostics, review status) and `renders/`. Preparation repairs are recorded and larger scale or shear defects stay invalid. Mesh expansion, physical-part accounting, assembly checks, contacts and visual review are separate evidence; `--contacts none` is the economical default, request `all` on an assembly before reuse. Rendering is resumable and cached by source hash.

Record a review only after opening the actual images, choose `reuse`, `adapt`, `technique` or `reject`, and list only the views you opened. `discover example` exports an editable plan with a placement guide for a reviewed `reuse`/`adapt` reference with passing checks; it does not certify buildability. If a reference has the wrong scale, rebuild its technique with real parts (`discover recipe` lists selected parameterized recipes). In the build notes identify the chosen precedent, modules studied, what was copied or rebuilt and what each contributes visually.
