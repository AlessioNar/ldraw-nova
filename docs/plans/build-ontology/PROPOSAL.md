# Build ontology as RDF: modelling approach

Design note for epic #89 (issue #90). It decides which existing vocabularies to reuse, where they fall short, the IRI scheme, the file format, and what is generated from what. It does not build anything; the child issues #91 to #97 do (numbering follows the epic's order: #91 roles, #92 components, #93 shapes, #94 JSON Schema, #95 templates, #96 vehicles, #97 export).

## Problem

The ontology of builds and build components lives in four hand-kept forms that must agree:

| form | where | holds |
|---|---|---|
| grammar schema | `ldraw_tools/grammar/schema.py`, `docs/plans/build-grammar/ontology.schema.json`, `ONTOLOGY.md` | components, parameters, ranges, defaults, validity rules |
| grammar roles | `ldraw_tools/grammar/roles.py` | 10 roles, definitions, `ALIASES` |
| classifier roles | `ldraw_tools/roles.py` | 16 roles, `CANONICAL` folding, `ROLE_WORDS` |
| templates | `docs/plans/build-templates/templates.spec.json` | reference builds, roles, knobs, source and licence |

Role words already drift between the two role lists (`tower` is a role in one and folds to `wall` in the other; `floor` is `structure` in one and `base` in the other). RDF makes one file the source of truth and the rest derived.

## Principles

1. Reuse before inventing. A term is minted in our namespace only when no standard vocabulary says it.
2. RDF is the source; JSON Schema, `params.md`, `ROLES`/`ALIASES`/`ROLE_WORDS` and the validity rules are generated and checked in CI.
3. No new runtime dependency in the app. `rdflib` and `pyshacl` are toolkit and build-time only; the app keeps consuming the generated JSON Schema.
4. Deterministic output: generated files are byte-stable (sorted, fixed prefixes, no timestamps).

## Vocabularies to reuse

| need | vocabulary | use | where it falls short |
|---|---|---|---|
| role vocabulary, aliases, hierarchy | **SKOS** | each role is a `skos:Concept` in one `skos:ConceptScheme`; `skos:prefLabel` is the canonical word, `skos:altLabel` the aliases ("tiles", "masonry"); `skos:definition` replaces the `ROLES` docstrings; `skos:broader` for hierarchy; `skos:exactMatch` / `skos:broadMatch` for the classifier's `CANONICAL` folding (`porch` → `trim`, `tower` → `wall`) | none for roles. Aliases carry no language or domain scoping, so "floor" meaning `structure` in buildings and `base` in vehicles needs one `altLabel` on the right concept plus a `skos:scopeNote`, or a per-domain scheme. Decided in #91. |
| component classes and parameters | **OWL** (RDFS where enough) | `Building`, `Footprint`, `Storeys`, `Walls`, `Openings`, `Corners`, `Roof`, `Details`, `Base` as `owl:Class`; part-of as an object property; parameters as datatype properties with `rdfs:range` and `rdfs:domain` | no notion of default values, numeric bounds, UI order, or "this is a colour". Ranges and defaults go on our own annotation properties (below); bounds use `xsd` facets via `owl:withRestrictions` only if a reasoner is ever needed, otherwise SHACL. OWL is open-world, so it cannot say "invalid". |
| validity and combination rules | **SHACL** | the hand-written rules in `schema.py` (`modular-fits`, window kind fits storey height, setbacks rectangles only, and so on) as `sh:NodeShape` with `sh:property`, `sh:in`, `sh:minInclusive`/`sh:maxInclusive`, and `sh:sparql` for cross-field rules | SHACL validates RDF graphs, not JSON. A plan is JSON-LD-ified by a fixed `@context` before validation. Rules with arithmetic over several fields need SHACL-SPARQL, which `pyshacl` supports but browsers do not run. Fine: validation stays server and toolkit side. |
| template provenance, licence and attribution | **PROV-O** with **DCTerms** | a template is a `prov:Entity` that `prov:wasDerivedFrom` the source set; `prov:wasAttributedTo` a `prov:Agent` (e.g. Marc Giraudet); `dcterms:license` points at the CCAL 2.0 IRI; `dcterms:title`, `dcterms:source`, `dcterms:description` for the catalogue fields | PROV-O has no "derived by the toolkit with these knobs" shortcut. Use `prov:Activity` for the derivation run only where #56 needs it; otherwise `wasDerivedFrom` plus `wasAttributedTo` is enough. |
| metadata of the ontology itself | **DCTerms**, `owl:Ontology`, `vann` | title, licence, version IRI, preferred prefix | none |
| a template as a product or place | schema.org `Product` / `Place` | only if a catalogue consumer wants it. Not used in v1 | schema.org is not designed for parametric or constructed objects; adds nothing the generators need |

Considered and not used: **QUDT** for units (studs, plates and LDU are three project-local units; one `obd:unit` annotation is cheaper than importing QUDT), **ifcOWL** or **BOT** (the Building Topology Ontology is the nearest prior art for Site/Building/Storey/Space; we borrow its names `Building` and `Storey` where they match but do not import it, because our components are construction stages, not spatial zones), **Dublin Core Terms for roles** (too coarse), **OWL-Time** (no time dimension).

## Our own terms (namespace `obd:`)

Kept to the minimum, all annotation or small helper properties:

| term | kind | meaning |
|---|---|---|
| `obd:default` | annotation property | default value of a parameter |
| `obd:unit` | annotation property | `stud`, `plate`, `course`, `ldu` |
| `obd:colourParameter` | class of parameter | marks an LDraw colour parameter (replaces `x-colour`) |
| `obd:component` | object property | the component a parameter belongs to (replaces `x-component`) |
| `obd:order` | annotation property | display order for the form UI |
| `obd:role` | object property | the role (a SKOS concept) a component's placements carry |

## IRI scheme

Persistent, hash-free, resolvable under the project's GitHub Pages base once published (#97). Until then the IRIs are identifiers only.

```
Base:        https://alessionar.github.io/open-brick-designer/ns/
Terms:       obd:   = <base>obd#            (classes and properties: obd:Building, obd:wallThickness)
Roles:       role:  = <base>role/           (concepts: role:roof, role:wall)
Templates:   tpl:   = <base>template/       (instances: tpl:small-house)
Agents:      agent: = <base>agent/          (people and sets credited in provenance)
```

Rules: classes `UpperCamelCase`, properties `lowerCamelCase`, role and template slugs are the existing lowercase ids so `role:roof` ↔ `'roof'` and `tpl:small-house` ↔ `'small-house'` map without a lookup table. Terms are never renamed; deprecate with `owl:deprecated true` and `dcterms:isReplacedBy`. If the repo is renamed or moved, only the base changes and a `w3id.org` redirect is the long-term fix (open question below).

## File format and layout

Turtle, one file per concern, in the repo:

```
ontology/
  obd.ttl            owl:Ontology header, component classes, parameters (#92)
  roles.ttl          SKOS concept scheme (#91)
  shapes.ttl         SHACL shapes (#93)
  templates.ttl      reference builds with PROV-O (#95)
  context.jsonld     JSON-LD context for turning a plan into a graph for SHACL
```

Turtle because it diffs well in review and `rdflib` round-trips it. Files are written by hand, then normalised by a serialiser in the toolkit (sorted subjects, fixed prefix order), so the CI check "regenerating changes nothing" is meaningful. Location `ontology/` at repo root, next to `data/`; design docs stay under `docs/plans/build-ontology/`.

## What is generated from what

```
roles.ttl ───────────► ldraw_tools/grammar/roles.py   ROLES, ALIASES, role_for_word
          └──────────► ldraw_tools/roles.py           CANONICAL, ROLE_WORDS (classifier keeps its own scoring)
obd.ttl ─────────────► docs/plans/build-grammar/ontology.schema.json   (JSON Schema 2020-12, same shape as today)
          └──────────► params.md, ONTOLOGY.md parameter tables
shapes.ttl ──────────► validity rules used by validate.py (pyshacl), and the rule table in docs
templates.ttl ───────► ldraw_tools/data/templates/*.json metadata (title, source, licence, families, kinds)
```

Direction is one way. Generated files carry a "generated from ontology/…, do not edit" header and are checked in so the app and users without `rdflib` still work. A CI step regenerates and fails on a diff (#97). The component-tree editor (#50) is unaffected because the JSON Schema keeps its shape, including `x-component` and `x-colour`, now emitted from `obd:component` and `obd:colourParameter`.

Migration order, so nothing is ever two sources of truth: #57 lands the single role module; #91 moves its content into `roles.ttl` and generates the module back out with identical `ROLES`/`ALIASES`; #92 and #94 do the same for the schema; #93 replaces the hand rules only after the six presets and the 20 presets validate under both.

## Decisions

1. SKOS for roles, OWL for classes, SHACL for rules, PROV-O + DCTerms for provenance. No other vocabulary imported.
2. Our namespace holds only defaults, units, UI order, and the component link; nothing that a standard term already says.
3. Turtle under `ontology/`, serialised deterministically; JSON Schema, params and role modules are generated and committed.
4. `rdflib` and `pyshacl` are dev/toolkit-only dependencies; the app and its runtime keep reading JSON.
5. No OWL reasoner in the pipeline. OWL is for documentation and class structure; validity is SHACL's job.

## Risks and open questions

- Does `pyshacl` run fast enough to validate generation inside the agent loop? Measure on the 20 presets in #93; fall back to keeping the Python rules as the runtime check and SHACL as the CI check if not.
- Domain-scoped aliases (`floor`): one scheme with `scopeNote`, or one scheme per domain (buildings, vehicles)? Needed before #91 merges; vehicles arrive with #96.
- A stable base IRI: GitHub Pages path above is a placeholder; a `w3id.org` registration would survive a rename. Owner's call.
- Licence of the ontology files: the repo's docs are CC BY-SA 4.0 (`ATTRIBUTION.md`); suggest the same for `ontology/` and say so in the `owl:Ontology` header. Template instance data keeps its source licence (CCAL 2.0) per instance.
- Whether to model parameter bounds in OWL as well as SHACL. Proposed no: one place (SHACL) avoids a second copy.

## Child issue map

| issue | depends on this note for |
|---|---|
| #91 roles as SKOS | scheme layout, alias policy, IRI scheme, `CANONICAL` mapped to `exactMatch`/`broadMatch` |
| #92 components and parameters as OWL | `obd:` helper terms, class names, file layout |
| #93 SHACL | shape file, the JSON-LD context, pyshacl as toolkit-only |
| #94 generate JSON Schema | one-way generation, unchanged schema shape |
| #95 templates with PROV-O | provenance pattern, licence per instance |
| #97 export and docs | publishing base IRI, CI sync check |
