"""
Orchestrate the AAS analysis of a single corpus:
  - Start the Neo4j Docker container
  - Upload the corpus in data/ if the graph is empty (JSON / XML / AASX)
  - Run every analysis step and save its output to results/<timestamp>/
  - Stop the container when done (unless --keep-running)

The pipeline analyses exactly one data directory. Everything it needs is found
relative to the working directory: data/ (the corpus), schemas/, idta-templates/,
deprecated-templates/ and results/.

Usage:
  python tool/orchestrate.py                                      # upload + all steps
  python tool/orchestrate.py --data-dir data --format xml         # force the format
  python tool/orchestrate.py --version 3.0                        # skip version detection
  python tool/orchestrate.py --steps schema_validation constraint_check
  python tool/orchestrate.py --reupload                           # wipe and re-upload
  python tool/orchestrate.py --keep-running                       # leave the container up
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(_TOOL_DIR))

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
from common import NEO4J_USER, NEO4J_PASSWORD

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The one corpus directory. Overridable on the command line and by environment,
# so the data can live outside the tree without editing this file.
DEFAULT_DATA_DIR = os.environ.get("AAS_DATA_DIR") or "data"

# The single compose service, its container name, and the bolt port it publishes.
# All three must match docker-compose.yml.
COMPOSE_SERVICE = os.environ.get("AAS_COMPOSE_SERVICE") or "neo4j"
CONTAINER = os.environ.get("AAS_CONTAINER") or "neo4j-aas"
DEFAULT_BOLT_PORT = int(os.environ.get("AAS_BOLT_PORT") or 7687)

# Current and withdrawn IDTA submodel templates, read off disk by
# submodel_analyser.py to classify each submodel's origin. Both are optional:
# without them every submodel is reported as Proprietary.
IDTA_TEMPLATES_DIR = os.environ.get("AAS_IDTA_TEMPLATES_DIR") or "idta-templates"
DEPRECATED_TEMPLATES_DIR = (
    os.environ.get("AAS_DEPRECATED_TEMPLATES_DIR") or "depricated-templates"
)

# Each entry: (script_filename, output_stem). Filenames only — they are resolved
# against _TOOL_DIR at launch, so the pipeline finds its own steps whatever the
# working directory is. Everything else the run touches stays relative to the
# working directory, because it is the user's, not the tool's.
ANALYSIS_SCRIPTS: list[tuple[str, str]] = [
    ("schema_validation.py",       "schema_version"),
    ("constraint_check.py",        "constraint_check"),
    ("asset.py",                   "asset"),
    ("metamodel.py",               "metamodel"),
    ("multilanguage.py",           "multilanguage"),
    ("statistical.py",             "statistical"),
    ("submodel_analyser.py",       "submodel-analyser"),
    ("semantic_identification.py", "semantic-identification"),
]

SUPPORTED_FORMATS = ("json", "xml", "aasx")

READY_TIMEOUT_S = 120


def detect_format(data_dir: str) -> str:
    """Guess the corpus format from the file extensions present in *data_dir*.

    Counts rather than first-hit, so a stray .json next to a thousand .xml files
    does not decide it. Recurses, because corpora are often one directory per
    shell.
    """
    root = Path(data_dir)
    if not root.is_dir():
        raise SystemExit(f"Data directory {data_dir!r} does not exist.")
    counts = {fmt: sum(1 for _ in root.rglob(f"*.{fmt}")) for fmt in SUPPORTED_FORMATS}
    best = max(counts, key=lambda f: counts[f])
    if counts[best] == 0:
        raise SystemExit(
            f"No .json, .xml or .aasx files found under {data_dir!r}. "
            f"Put the corpus there, or pass --data-dir."
        )
    return best


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def container_start() -> None:
    print(f"  [docker] starting {CONTAINER} ...")
    subprocess.run(
        ["docker", "compose", "up", "-d", COMPOSE_SERVICE],
        check=True,
    )


def container_stop() -> None:
    print(f"  [docker] stopping {CONTAINER} ...")
    subprocess.run(
        ["docker", "compose", "stop", COMPOSE_SERVICE],
        check=False,
    )


def container_remove() -> None:
    print(f"  [docker] removing {CONTAINER} (volumes will be deleted on next up) ...")
    subprocess.run(
        ["docker", "compose", "rm", "-f", "-v", COMPOSE_SERVICE],
        check=False,
    )


def container_state() -> str:
    """Return the container's docker State.Status (e.g. running, exited, '' if unknown)."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def container_logs_tail(lines: int = 25) -> str:
    result = subprocess.run(
        ["docker", "logs", "--tail", str(lines), CONTAINER],
        capture_output=True, text=True, check=False,
    )
    return (result.stdout + result.stderr).strip()


# ---------------------------------------------------------------------------
# Neo4j readiness check
# ---------------------------------------------------------------------------

def wait_for_neo4j(uri: str, timeout: int = READY_TIMEOUT_S,
                   max_restarts: int = 3) -> AASNeo4JClient:
    """Retry connecting to Neo4j until it is ready or timeout expires.

    Neo4j containers intermittently abort during early boot (exit 3, e.g. a
    transient store-lock) and never open bolt. Rather than retrying for the full
    timeout against a dead container, detect the exited state and restart it
    (`restart: on-failure` in compose usually handles this first). On final
    failure, surface the container log tail instead of a bare connection error.
    """
    deadline = time.time() + timeout
    last_exc = None
    restarts = 0
    while time.time() < deadline:
        try:
            client = AASNeo4JClient(uri, NEO4J_USER, NEO4J_PASSWORD, AAS_NEO4J_MODEL_CONFIG)
            client.execute_clause("RETURN 1", True)
            print(f"  [neo4j] ready at {uri}")
            return client
        except Exception as exc:
            last_exc = exc
            state = container_state()
            if state == "exited" and restarts < max_restarts:
                restarts += 1
                print(f"  [neo4j] {CONTAINER} exited during boot — restart {restarts}/{max_restarts}")
                print(f"  [neo4j] last logs:\n{container_logs_tail()}")
                container_start()
            time.sleep(3)
    raise RuntimeError(
        f"Neo4j at {uri} not ready after {timeout}s: {last_exc}"
        f"\n  last logs:\n{container_logs_tail()}"
    )


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def print_upload_stats(stats) -> None:
    print("  [upload] done")
    print(f"  [upload] files processed   : {stats.total_files}")
    print(f"  [upload] nodes created     : {stats.total_nodes_created}")
    print(f"  [upload] relationships     : {stats.total_relationships_created}")
    print(f"  [upload] total time        : {stats.total_time:.1f}s")
    print(f"  [upload] processing time   : {stats.total_processing_time:.1f}s")
    print(f"  [upload] db node time      : {stats.total_node_creation_time:.1f}s")
    print(f"  [upload] db relation time  : {stats.total_relationship_creation_time:.1f}s")


def upload_data(client: AASNeo4JClient, data_dir: str, fmt: str) -> None:
    print(f"  [upload] uploading {fmt.upper()} files from {data_dir} ...")
    client.optimize_database()
    if fmt == "json":
        stats = client.upload_all_json_from_dir(data_dir)
    elif fmt == "xml":
        stats = client.upload_all_xml_from_dir(data_dir)
    elif fmt == "aasx":
        from aas_mapping.aas_neo4j_adapter.aasx import AasxToNeo4jImporter
        stats = AasxToNeo4jImporter(client).upload_all_aasx_from_dir(data_dir)
    else:
        raise ValueError(f"Unknown format: {fmt}")
    print_upload_stats(stats)


# ---------------------------------------------------------------------------
# Analysis runner
# ---------------------------------------------------------------------------

def run_analysis(bolt_uri: str, results_dir: Path, data_dir: str, fmt: str, version: str | None,
                 steps: list[str] | None = None) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)

    detected_version_file = results_dir / ".detected_version"

    # PYTHONIOENCODING: analysis output is captured as UTF-8; without this a Windows
    # cp1252 stdout crashes json.dump on non-Latin-1 chars (e.g. a registered-trade
    # mark sign in a URL).
    env = {**os.environ, "NEO4J_BOLT_URI": bolt_uri, "AAS_DATA_DIR": data_dir, "AAS_FORMAT": fmt,
           "AAS_DETECTED_VERSION_FILE": str(detected_version_file),
           "AAS_IDTA_TEMPLATES_DIR": IDTA_TEMPLATES_DIR,
           "AAS_DEPRECATED_TEMPLATES_DIR": DEPRECATED_TEMPLATES_DIR,
           "PYTHONIOENCODING": "utf-8"}
    if version is not None:
        env["AAS_VERSION"] = version

    scripts_to_run = list(ANALYSIS_SCRIPTS)
    if steps:
        steps_lower = {s.lower() for s in steps}
        # match by script filename stem OR output stem (e.g. submodel-analyser)
        scripts_to_run = [
            entry for entry in scripts_to_run
            if Path(entry[0]).stem.lower() in steps_lower or entry[1].lower() in steps_lower
        ]
        if not scripts_to_run:
            available = ", ".join(stem for _, stem in ANALYSIS_SCRIPTS)
            print(f"  [analysis] WARNING: no scripts matched steps {steps}. Available: {available}")
            return

    log_path = results_dir / "run.log"
    for script, out_stem in scripts_to_run:
        out_path = results_dir / f"{out_stem}.json"
        print(f"  [analysis] running {script} -> {out_path}")
        result = subprocess.run(
            [sys.executable, str(_TOOL_DIR / script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        out_path.write_text(result.stdout, encoding="utf-8")
        if result.stderr:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n[{script}]\n{result.stderr}")
        if result.returncode != 0:
            print(f"  [analysis] WARNING: {script} exited with code {result.returncode}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AAS corpus analysis orchestrator")
    parser.add_argument("--data-dir", metavar="DIR", default=DEFAULT_DATA_DIR,
                        help=f"Directory holding the corpus (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--format", metavar="FMT", choices=SUPPORTED_FORMATS,
                        help="Corpus format: json, xml or aasx "
                             "(default: detected from the files present)")
    parser.add_argument("--version", metavar="V",
                        help="Known metamodel version, e.g. 3.0 or 3.1. Used when the schema step "
                             "detects nothing; leaving it unset on a V3.0 corpus understates "
                             "AASd-002 violations.")
    parser.add_argument("--bolt-port", metavar="PORT", type=int, default=DEFAULT_BOLT_PORT,
                        help=f"Host bolt port published by the container "
                             f"(default: {DEFAULT_BOLT_PORT})")
    parser.add_argument("--run-dir", metavar="TIMESTAMP",
                        help="Reuse an existing results/<TIMESTAMP>/ folder instead of creating a "
                             "new one (for resuming an interrupted run)")
    parser.add_argument("--keep-running", action="store_true",
                        help="Do not stop the container after analysis")
    parser.add_argument("--prune", action="store_true",
                        help="Remove the container and its volumes before running, forcing a full "
                             "re-upload")
    parser.add_argument("--reupload", action="store_true",
                        help="Clear the database and re-upload data even if already present")
    parser.add_argument("--upload-only", action="store_true",
                        help="Upload data only, skip analysis")
    parser.add_argument("--steps", metavar="STEP", nargs="+",
                        help="Run only the named analysis step(s), e.g. --steps schema_validation "
                             "constraint_check. Available: "
                             f"{', '.join(Path(s).stem for s, _ in ANALYSIS_SCRIPTS)}")
    args = parser.parse_args()

    data_dir = args.data_dir
    fmt = args.format or detect_format(data_dir)
    bolt_uri = f"bolt://localhost:{args.bolt_port}"

    if args.prune:
        print("Pruning container and volumes ...")
        container_stop()
        container_remove()
        print("Prune complete.\n")

    run_timestamp = args.run_dir or datetime.now().strftime("%d-%m-%Y_%H-%M")
    results_dir = Path("results") / run_timestamp

    print(f"\n{'='*60}")
    print(f"  Data directory: {data_dir}")
    print(f"  Container:      {CONTAINER}  ({bolt_uri})")
    print(f"  Format:         {fmt.upper()}{'' if args.format else '  (detected)'}")
    print(f"  Version:        {args.version or 'unknown'}")
    print(f"  Results:        {results_dir}/")
    print(f"{'='*60}")

    try:
        container_start()
        client = wait_for_neo4j(bolt_uri)

        if client.execute_clause("MATCH (n) RETURN count(n) AS count", True)["count"] > 0:
            if args.reupload:
                print("  [upload] --reupload set — clearing database ...")
                client._remove_all()
                client._remove_all_indexes_and_constraints()
                upload_data(client, data_dir, fmt)
            else:
                print("  [upload] data already present — skipping upload")
        else:
            upload_data(client, data_dir, fmt)

        if not args.upload_only:
            run_analysis(bolt_uri, results_dir, data_dir, fmt, args.version, steps=args.steps)
            print(f"  [done] results saved to {results_dir}/")
        else:
            print("  [done] upload complete, skipping analysis")

    except Exception as exc:
        print(f"  [ERROR] run failed: {exc}")

    finally:
        if not args.keep_running:
            container_stop()


if __name__ == "__main__":
    main()
