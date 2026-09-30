# AAS schemas — supply your own

No schemas are shipped here. The AAS metamodel schemas are IDTA's to distribute,
so this repository ships the directory layout and nothing else. Drop your own
copies in and the schema step runs; leave them out and it reports itself as
skipped, without failing the pipeline.

## Expected layout

```
schemas/
  json/
    3-0.json      # AAS JSON Schema, metamodel V3.0
    3-1.json      # AAS JSON Schema, metamodel V3.1
  xml/
    3-0.xsd       # AAS XSD, metamodel V3.0
    3-1.xsd       # AAS XSD, metamodel V3.1
```

The filenames are matched exactly (`tool/schema_validation.py`,
`_EXPECTED_SCHEMAS`). The directory is `schemas/` relative to the working
directory — the repository root, since that is where the pipeline is run from.
Only the versions a run actually checks need to be present: with
`orchestrate.py --version 3.0`, that one schema is enough.

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
intend to check.

## What happens without them

`schema_validation.py` emits `{"skipped": true, "reason": ..., "missing_schemas":
[...]}` and writes no detected version. `constraint_check.py` then falls back to
`--version` and reports `"version_source": "known"`.

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
