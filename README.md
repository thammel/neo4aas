# AAS corpus analysis

A pipeline that loads one corpus of Asset Administration Shells into Neo4j and
characterises it: schema conformance, constraint violations, asset and metamodel
usage, multilanguage coverage, submodel origins and semanticId sourcing.

It carries **no library code** — the graph mapping comes from `aas4graph`,
installed as a dependency.

```
tool/                 the analysis pipeline
data/                 the corpus you supply (ships empty, gitignored)
schemas/              AAS schemas you supply (ships empty; see schemas/README.md)
idta-templates/       current IDTA templates you supply (ships empty; see its README)
depricated-templates/ withdrawn IDTA templates you supply (ships empty; see its README)
docker-compose.yml    the single Neo4j container
april_2026/           anonymised results, April 2026 run (text format)
june_2026/            anonymised results, June 2026 run (JSON format)
```

One run analyses one data directory against one container. `april_2026/` and
`june_2026/` are archived outputs from earlier multi-corpus runs, kept for
reference; the corpora behind them are vendor-supplied and are not published, so
each is referred to by an id only.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
cd tool/ajv && npm install && cd -   # JSON validation via AJV
```

`pip install -e .` pulls `aas4graph` from a pinned commit of
`github.com/thammel/neo4aas`.

### Your data

Put the corpus in `data/` — JSON, XML or AASX files, nested however you like.
`data/` is gitignored apart from its `.gitkeep`. The format is detected from the
extensions present; override it with `--format` if the directory is mixed.

To keep the corpus outside the tree, point `--data-dir` (or `AAS_DATA_DIR`) at
any directory.

### Schemas

None are shipped — they are IDTA's to distribute. Drop your own into
`schemas/json/` and `schemas/xml/`; see `schemas/README.md` for the expected
filenames and for what the pipeline does without them (it skips the schema step,
and constraint results then report `"version_source": "known"` or `"unknown"`).

### IDTA templates

Also unshipped, same reasoning. `submodel_analyser` classifies each submodel's
origin as `IDTA`, `Deprecated` or `Proprietary` by matching its semanticId
against the templates it reads off disk:

- `idta-templates/` — the current edition of each IDTA submodel template
- `depricated-templates/` — the superseded editions

Both as flat AAS Environment JSON, both optional. Without them that
classification degrades to `Proprietary` or is omitted. See each directory's
README.

## Running

```bash
python tool/orchestrate.py                    # start container, upload, all steps
python tool/orchestrate.py --version 3.0      # skip version detection
python tool/orchestrate.py --steps schema_validation constraint_check
python tool/orchestrate.py --reupload         # wipe the graph and load again
python tool/orchestrate.py --keep-running     # leave the container up afterwards
```

`orchestrate.py` starts and stops the container itself; `docker compose up -d`
by hand works too. The graph lives in a named volume, so a second run skips the
upload unless you pass `--reupload` or `--prune`.

Output lands in `results/<timestamp>/`, which is gitignored.

### Steps

| step | output |
|------|--------|
| `schema_validation` | `schema_version.json` |
| `constraint_check` | `constraint_check.json` |
| `asset` | `asset.json` |
| `metamodel` | `metamodel.json` |
| `multilanguage` | `multilanguage.json` |
| `statistical` | `statistical.json` |
| `submodel_analyser` | `submodel-analyser.json` |
| `semantic_identification` | `semantic-identification.json` |

Every step but `schema_validation` reads only the graph. `schema_validation` is
the one that touches the files on disk, and the only one that can skip itself.

### Memory

A large corpus needs more than the defaults in `docker-compose.yml`. Every
tunable there carries a comment; `constraint_check` is the step that forces the
heap and transaction limits up.
