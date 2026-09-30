# AAS schemas — supply your own

No schemas are shipped here. The AAS metamodel schemas are IDTA's to distribute,
so this repository ships the directory layout and nothing else. Drop your own
copies in and the schema step runs; leave them out and it reports itself as
skipped, without failing the pipeline.

## Layout

Two directories, one per serialisation. JSON schemas go in `json/` as `*.json`,
XSDs in `xml/` as `*.xsd`. **Every file found is used** — there is no fixed list
of filenames.

```
schemas/
  json/
    3-0.json          # AAS JSON Schema, metamodel V3.0
    3-1.json          # AAS JSON Schema, metamodel V3.1
    custom.json       # anything else you want checked
  xml/
    3-0.xsd           # AAS XSD, metamodel V3.0
    3-1.xsd           # AAS XSD, metamodel V3.1
    my-profile.xsd    # anything else you want checked
```

The corpus is validated once per schema and each gets its own entry in
`schema_version.json`. Anything without the right extension is ignored, so a
`README.md` or `.gitkeep` next to the schemas costs nothing.

### Filenames that name a version

A filename of the form `<major>-<minor>` — `3-0`, `3-1`, `3-2`, also written
`3_0` or `3.0` — is read as a metamodel version. Only those take part in
**version detection**: the one with the fewest violations becomes
`detected_version`, which `constraint_check.py` uses.

Any other name is a **custom schema**. It is validated and reported under its own
stem (`custom.json` → `"schema_label": "custom AJV"`, `"version": null`), but can
never become the detected version.

### With `--version` set

`orchestrate.py --version 3.0` pins which version-named schema runs — the others
would cost a full pass over the corpus for nothing. Custom schemas run either
way. So one version schema is enough, and a directory holding nothing but custom
schemas still works (`detected_version` is then `null` and the constraint checker
falls back to `--version`).

To keep the schemas outside the tree, point `AAS_SCHEMAS_DIR` at any directory
laid out as above:

```bash
AAS_SCHEMAS_DIR=/path/to/aas-schemas python tool/orchestrate.py
```

Both `schemas/json/` and `schemas/xml/` are gitignored apart from their
`.gitkeep`, so a local copy cannot be committed by accident.

## Where to get them

They are published with the metamodel specification (IDTA-01001-3-0 and
IDTA-01001-3-1) and mirrored in the `admin-shell-io/aas-specs` repository under
its `schemas/` directory. Take the JSON Schema and the XSD for each version you
intend to check, and rename each to its version (`3-0.json`, `3-1.xsd`) so it
takes part in version detection.

## What happens without them

With neither directory holding a usable schema, `schema_validation.py` emits
`{"skipped": true, "reason": ..., "schemas_found": [...]}` and writes no detected
version. `constraint_check.py` then falls back to `--version` and reports
`"version_source": "known"`.

Without `--version` either, `version_source` is
`"unknown"` and `AASConstraintChecker` applies the V3.1 idShort pattern by
default. On a V3.0 corpus this **understates AASd-002 violations** — V3.0 forbids
the hyphen, V3.1 permits it. Pass `--version 3.0` on a V3.0 corpus, or supply
the schemas.

## JSON validation also needs Node

The JSON path validates through AJV in a Node worker:

```bash
cd tool/ajv && npm install
```

That npm project is found independently of this directory, so moving the schemas
elsewhere with `AAS_SCHEMAS_DIR` does not affect it.

The XML path uses `lxml` and needs no Node.
