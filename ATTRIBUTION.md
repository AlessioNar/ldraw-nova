# Attribution

Copyright (c) 2026 Carlos Antelo

The research notes, documentation, and other materials in this repository are
licensed under **CC BY-SA 4.0** (see `CC-BY-SA-4.0` file).

## How to attribute
When reusing or adapting this material, please provide attribution as:

- Author: Carlos Antelo
- Project: ldraw-nova
- Source: https://github.com/anteloc/ldraw-nova
- License: CC BY-SA 4.0
- Indicate changes: yes (if you modified the material)

If you cite this work in writing, a suggested citation can be found at:

> anteloc. *ldraw-nova - Agent tooling for generative LEGO model building, powered by Jev*. GitHub repository, 2026.

## Technic construction knowledge

The Technic design guide, pattern notes and `examples/technic-studies` adapt
Carlos Antelo's **ldraw-mecha** documentation and examples (2026), licensed under
CC BY-SA 4.0. The source project is https://github.com/anteloc/ldraw-mecha.
Changes include construction-focused summaries, explicit source qualifications,
Nova workflow integration and new design prompts. Three source previews are
retained unchanged. [The study index](examples/technic-studies/README.md) and
[source manifest](examples/technic-studies/sources.json) record exact provenance.

The underlying 42083/42110 LDraw models credit Philippe Hurbain [Philo] and retain
their CCAL 2.0 notices. Documentation reuse does not replace model or part licences.

## BrickLink colour table

`ldraw_tools/data/bricklink-colours.json` (BrickLink colour id and name to LDraw colour code, used by the BrickLink and
BrickStore importers and the BrickLink wanted-list export) pairs colours using the `legoCols` table of the R package
[legocolors](https://cran.r-project.org/package=legocolors) 0.4.0 (MIT licence, Copyright (c) 2019 Matthew Leonawicz),
completed by colour name from the Rebrickable mapping above. `tools/build_bricklink_colours.py` regenerates it. Colours
with no certain LDraw equivalent are listed as unmapped in the file and are never guessed.

## Rebrickable data

`ldraw_tools/data/set-catalog.bin` (set numbers, names, years, themes, piece counts and parts lists of about 20,000
LEGO sets, read with `ldraw-agent sets`) and `ldraw_tools/data/rebrickable-colours.json` / `rebrickable-parts.json`
(Rebrickable to LDraw colour and part mapping) are derived from the database downloads of
[Rebrickable](https://rebrickable.com/downloads/).

**Data from Rebrickable, rebrickable.com.**

Rebrickable's downloads page states (read 2026-10-21): "Free for personal and commercial use, provided you
acknowledge Rebrickable as your data source." This notice is that acknowledgement; keep it when you redistribute the
files or derivatives. The terms are recorded in `docs/plans/done/set-catalog.md`.

### Set pictures

Pictures of sets are not part of this repository. Unless the owner switches off remote fetching (Settings, or
`LDRAW_NOVA_SET_IMAGE_FETCH=0`), the backend downloads the picture of a set the owner has from Rebrickable's image
CDN (`cdn.rebrickable.com/media/sets/<set number>.jpg`) once and keeps it under `<data>/cache/set-images/` on that
machine, for offline use. These cached files are never committed or redistributed. **Images and data from Rebrickable,
rebrickable.com.** Pictures the owner uploads stay on the owner's machine too.

LEGO(R) is a trademark of the LEGO Group of companies, which does not sponsor, authorize or endorse this project.

## CaDA set list (local, not distributed)

`tools/fetch_cada_sets.py` lets the owner fetch the list of CaDA set numbers, names, years and piece counts from
[Merlins Bricks](https://www.merlinsbricks.com/setdb/brand/cada/) once, into `data/local/set-catalog-local.json`.
The site is copyrighted (Kai Zimmermann) and publishes no licence, so that list is local user data: the path is
gitignored, nothing fetched is committed or bundled, and the tool makes three requests per run (robots.txt, the
brand page, one JSON file), honours robots.txt, and refuses to run twice without `--force`. CaDA is a trademark of
its owner, which does not sponsor or endorse this project.
