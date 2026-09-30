# Withdrawn IDTA submodel templates — supply your own

Nothing is shipped here. These are IDTA downloads and theirs to distribute, so
this repository ships the directory and nothing else — the same arrangement as
`data/`, `schemas/` and `idta-templates/`.

Drop the **withdrawn** template editions in as flat AAS Environment JSON files:

```
depricated-templates/
  IDTA-02003-1-2_Technical-Data.json
  IDTA-02006-2-0_Digital-Nameplate.json
  ...
```

Only `*.json` directly in this directory is read — the glob does not recurse.
Filenames do not matter.

## What reads it

`submodel_analyser.py`, straight off disk. It collects
`submodels[].semanticId.keys[].value` from every file into the set of withdrawn
template ids, then labels each submodel in the corpus as `IDTA`, `Deprecated`
or `Proprietary`. The current editions come from `idta-templates/` the same way.

`idta-templates/` is checked **first**, so a semanticId present in both sets
counts as `IDTA`. Keep current editions out of here.

## What happens without it

`load_deprecated_semantic_ids()` returns an empty set and the step continues:
`submodel-analyser.json` reports `"deprecated_template_ids_available": false`
and every submodel built on a withdrawn template is counted as `Proprietary`
instead of `Deprecated`. The published `june_2026/` results were produced with
the directory populated — all eleven report `true` — so a run without it will
not reproduce their `origin` distribution.

## Where to get them

From the IDTA submodel template downloads, taking the superseded edition of each
template rather than the current one — the current editions belong in
`idta-templates/`.

Everything but this file and `.gitkeep` is gitignored, so a local copy cannot be
committed by accident.
