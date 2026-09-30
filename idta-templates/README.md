# Current IDTA submodel templates — supply your own

Nothing is shipped here. These are IDTA downloads and theirs to distribute, so
this repository ships the directory and nothing else — the same arrangement as
`data/`, `schemas/` and `depricated-templates/`.

Drop the **current** edition of each template in as flat AAS Environment JSON
files:

```
idta-templates/
  IDTA-02003-1-2_Technical-Data.json
  IDTA-02006-3-0_Digital-Nameplate.json
  ...
```

Only `*.json` directly in this directory is read — the glob does not recurse.
Filenames do not matter.

## What reads it

`submodel_analyser.py`, straight off disk. It collects
`submodels[].semanticId.keys[].value` from every file into the set of current
IDTA template ids, then labels each submodel in the corpus as `IDTA`,
`Deprecated` or `Proprietary`. The withdrawn editions come from
`depricated-templates/` the same way.

This directory is checked **first**, so a semanticId present in both sets counts
as `IDTA`, not `Deprecated`. Keep withdrawn editions out of here.

## What happens without it

`load_idta_semantic_ids()` returns an empty set and the step continues:
`submodel-analyser.json` reports `"idta_template_ids_available": false`. With
`depricated-templates/` also empty the `origin` field is omitted entirely;
with only this one empty, every IDTA-based submodel counts as `Proprietary`.

## Where to get them

From the IDTA submodel template downloads, taking the current edition of each
template. The metamodel version of the template files themselves does not
matter — only their submodel semanticIds are read.

Everything but this file and `.gitkeep` is gitignored, so a local copy cannot be
committed by accident.
