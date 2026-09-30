"""
Schema validation — first step of the analysis pipeline.

Validates all files against the AAS 3.0 and 3.1 schemas and reports
conformance statistics. Writes the detected (or known) version to
AAS_DETECTED_VERSION_FILE so the subsequent constraint_check.py can use it.

Reads environment variables (set by orchestrate.py):
  AAS_DATA_DIR              — path to the corpus directory
  AAS_FORMAT                — one of: json, xml, aasx
  AAS_VERSION               — known version ("3.0" / "3.1"); if set, only that
                              schema is checked and no heuristic is used
  AAS_DETECTED_VERSION_FILE — path where the detected version is written
  AAS_SCHEMAS_DIR           — directory holding the AAS schemas (default:
                              schemas/ at the repository root)

No schemas are shipped with this repository — they are IDTA's to distribute. Drop
your own into schemas/json/ (any *.json) and schemas/xml/ (any *.xsd), or point
AAS_SCHEMAS_DIR at a directory laid out that way. Every file found is validated
against and reported separately; filenames that name a metamodel version
(3-0.json, 3-1.xsd, ...) additionally take part in version detection, and any
other name (custom.json, my-profile.xsd, ...) is reported under its own stem.
With no schema at all this step reports itself as skipped and the pipeline
continues; see the note on constraint_check.py in README.md.
"""

import io
import json
import logging
import os
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from lxml import etree

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

from tool.ajv.AjvValidatorWorker import AjvValidatorWorker
from common import emit, pct


# ---------------------------------------------------------------------------
# Schema paths
# ---------------------------------------------------------------------------

# schemas/ holds user-supplied files, so it is resolved against the working
# directory — deriving it from __file__ would point into site-packages on a
# non-editable install.
_DEFAULT_SCHEMAS_DIR = Path("schemas")
_SCHEMAS_DIR      = Path(os.environ.get("AAS_SCHEMAS_DIR") or _DEFAULT_SCHEMAS_DIR)
_JSON_SCHEMAS_DIR = _SCHEMAS_DIR / "json"
_XML_SCHEMAS_DIR  = _SCHEMAS_DIR / "xml"

# The AJV worker's npm project. Deliberately not derived from _SCHEMAS_DIR: that
# directory is user-supplied and may sit anywhere via AAS_SCHEMAS_DIR, while the
# Node install always lives next to the worker script — which is why this one is
# the exception that does resolve from __file__.
_AJV_DIR = Path(__file__).resolve().parent / "ajv"

# The extension each serialisation's schemas carry. Anything else in the directory
# is ignored, so a stray README or .gitkeep costs nothing.
_SCHEMA_SUFFIX = {"json": ".json", "xml": ".xsd"}

# A filename that names a metamodel version — "3-0", "3_1", "3.2". Such a file takes
# part in version detection and can be pinned with AAS_VERSION. Every other name is
# still validated against, but only ever reported under its own stem: handing
# "custom" to constraint_check.py as a version would break its idShort pattern
# selection silently.
_VERSION_FILENAME_RE = re.compile(r"^(\d+)[-_.](\d+)$")


class SchemaFile(NamedTuple):
    path: Path
    version: str | None  # "3.0" when the filename names a version, else None
    label: str           # the version, or the file stem


def _discover_schemas(kind: str) -> list[SchemaFile]:
    """Every schema of *kind* present in the schemas directory, in filename order."""
    root = _JSON_SCHEMAS_DIR if kind == "json" else _XML_SCHEMAS_DIR
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob(f"*{_SCHEMA_SUFFIX[kind]}")):
        match = _VERSION_FILENAME_RE.match(path.stem)
        version = f"{match.group(1)}.{match.group(2)}" if match else None
        found.append(SchemaFile(path, version, version or path.stem))
    return found


def _select_schemas(kind: str, known_version: str | None) -> tuple[list[SchemaFile],
                                                                  list[SchemaFile]]:
    """Return (schemas to validate against, everything discovered).

    A known version pins which of the version-named schemas is checked — running the
    others would waste a full pass over the corpus to no purpose. Custom-named
    schemas are not version alternatives, so they run either way.
    """
    discovered = _discover_schemas(kind)
    if not known_version:
        return discovered, discovered
    selected = [s for s in discovered if s.version is None or s.version == known_version]
    return selected, discovered


def _emit_schema_skip(kind: str, fmt: str, discovered: list[SchemaFile],
                      known_version: str | None) -> None:
    """Report the step as skipped rather than failed.

    The AAS_DETECTED_VERSION_FILE handoff is deliberately *not* written: nothing
    was detected. constraint_check.py then falls back to AAS_VERSION and labels
    its output version_source="known". With AAS_VERSION unset too it falls back
    further to the V3.1 idShort pattern, which understates AASd-002 on a V3.0
    corpus — that case surfaces as version_source="unknown".
    """
    root = _JSON_SCHEMAS_DIR if kind == "json" else _XML_SCHEMAS_DIR
    if discovered:
        reason = (f"no {_SCHEMA_SUFFIX[kind]} schema in {root} matches version "
                  f"{known_version}")
    else:
        reason = f"no {_SCHEMA_SUFFIX[kind]} schema files in {root}"
    emit({
        "skipped": True,
        "reason": reason,
        "schemas_dir": str(root),
        "schemas_found": [s.path.name for s in discovered],
        "format": fmt,
        "known_version": known_version,
        "detected_version": known_version,
    })


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_xsd(path: Path) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(path)))


# ---------------------------------------------------------------------------
# Violation grouping
# ---------------------------------------------------------------------------

def _violation_label(error: dict) -> str:
    keyword = error.get("keyword", "unknown")
    params  = error.get("params") or {}
    message = error.get("message", "")
    if keyword == "required":
        return f"required — {params.get('missingProperty', '?')}"
    if keyword == "additionalProperties":
        return f"additionalProperties — {params.get('additionalProperty', '?')}"
    return f"{keyword} — {message}"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_against_schema(schema: dict, files: list[Path]) -> tuple[int, int, Counter]:
    schema_valid = 0
    schema_invalid = 0
    violation_counter: Counter = Counter()

    with AjvValidatorWorker(schema) as worker:
        for path in files:
            try:
                json_string = path.read_text(encoding="utf-8")
            except Exception:
                schema_invalid += 1
                continue
            try:
                is_valid_json, complies_with_schema, errors = worker.validate(json_string)
            except Exception:
                schema_invalid += 1
                continue
            if not is_valid_json:
                schema_invalid += 1
                continue
            if complies_with_schema:
                schema_valid += 1
            else:
                schema_invalid += 1
                for err in errors:
                    violation_counter[_violation_label(err)] += 1

    return schema_valid, schema_invalid, violation_counter


def _validate_xml_against_schema(xsd: etree.XMLSchema, files: list[Path]) -> tuple[int, int, Counter]:
    schema_valid = 0
    schema_invalid = 0
    violation_counter: Counter = Counter()

    for path in files:
        try:
            doc = etree.parse(str(path))
        except etree.XMLSyntaxError:
            schema_invalid += 1
            continue
        if xsd.validate(doc):
            schema_valid += 1
        else:
            schema_invalid += 1
            for err in xsd.error_log:
                violation_counter[err.message] += 1

    return schema_valid, schema_invalid, violation_counter


def _find_aasx_data(zf: zipfile.ZipFile) -> tuple[str | None, bytes | None]:
    names = set(zf.namelist())
    if "[Content_Types].xml" in names:
        try:
            ct_root = etree.fromstring(zf.read("[Content_Types].xml"))
            ns = "http://schemas.openxmlformats.org/package/2006/content-types"
            for override in ct_root.findall(f"{{{ns}}}Override"):
                ct   = override.get("ContentType", "")
                part = override.get("PartName", "").lstrip("/")
                if ct == "application/aas+json" and part in names:
                    return "json", zf.read(part)
                if ct == "application/aas+xml" and part in names:
                    return "xml", zf.read(part)
        except Exception:
            pass
    for name in sorted(names):
        low = name.lower()
        if low.endswith(".json"):
            return "json", zf.read(name)
        if low.endswith(".xml") and not low.startswith("[") and "_rels" not in low:
            return "xml", zf.read(name)
    return None, None


def _validate_json_bytes_against_schema(
    schema: dict, entries: list[tuple[Path, bytes]]
) -> tuple[int, int, Counter]:
    schema_valid = 0
    schema_invalid = 0
    violation_counter: Counter = Counter()
    with AjvValidatorWorker(schema) as worker:
        for _, data in entries:
            try:
                json_string = data.decode("utf-8")
            except Exception:
                schema_invalid += 1
                continue
            try:
                is_valid_json, complies_with_schema, errors = worker.validate(json_string)
            except Exception:
                schema_invalid += 1
                continue
            if not is_valid_json:
                schema_invalid += 1
                continue
            if complies_with_schema:
                schema_valid += 1
            else:
                schema_invalid += 1
                for err in errors:
                    violation_counter[_violation_label(err)] += 1
    return schema_valid, schema_invalid, violation_counter


def _validate_xml_bytes_against_schema(
    xsd: etree.XMLSchema, entries: list[tuple[Path, bytes]]
) -> tuple[int, int, Counter]:
    schema_valid = 0
    schema_invalid = 0
    violation_counter: Counter = Counter()
    for _, data in entries:
        try:
            doc = etree.parse(io.BytesIO(data))
        except etree.XMLSyntaxError:
            schema_invalid += 1
            continue
        if xsd.validate(doc):
            schema_valid += 1
        else:
            schema_invalid += 1
            for err in xsd.error_log:
                violation_counter[err.message] += 1
    return schema_valid, schema_invalid, violation_counter


# ---------------------------------------------------------------------------
# Result building + version selection
# ---------------------------------------------------------------------------

_SchemaResult = tuple[SchemaFile, tuple[int, int, Counter]]


def _build_schema_output(
    results: list[_SchemaResult],
    known_version: str | None,
    violation_label: str,
) -> tuple[str | None, list[dict]]:
    """Return (effective_version, schema_results list for JSON output).

    Only version-named schemas can become the effective version; a custom schema is
    an extra conformance check, not a candidate metamodel version. With none of them
    present the version stays None and constraint_check.py falls back to AAS_VERSION.
    """
    versioned = [(s, r) for s, r in results if s.version is not None]
    if known_version and any(s.version == known_version for s, _ in versioned):
        effective_version = known_version
    elif versioned:
        # Fewest violations wins; the version label breaks ties deterministically.
        effective_version = min(
            versioned, key=lambda e: (sum(e[1][2].values()), e[0].version)
        )[0].version
    else:
        effective_version = None

    schema_results = []
    for schema, (valid, invalid, counter) in results:
        total = valid + invalid
        prefix = f"AAS {schema.version}" if schema.version else schema.label
        schema_results.append({
            "schema_label": f"{prefix} {violation_label.upper()}",
            "version": schema.version,
            "schema_file": schema.path.name,
            "valid_count": valid,
            "invalid_count": invalid,
            "valid_pct": pct(valid, total),
            "total_violations": sum(counter.values()),
            "violations": [{"type": vtype, "count": cnt} for vtype, cnt in counter.most_common()],
        })

    return effective_version, schema_results


class _StepError(Exception):
    """Carries the payload for an early `emit()` out of a nested validation loop."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("error", ""))
        self.payload = payload


def _run_schemas(schemas: list[SchemaFile], load, validate) -> list[_SchemaResult]:
    """Validate the corpus once per schema in *schemas*, preserving their order."""
    results: list[_SchemaResult] = []
    for schema in schemas:
        try:
            compiled = load(schema.path)
        except Exception as exc:
            raise _StepError({"error": f"could not load schema {schema.path}: {exc}"}) from exc
        try:
            results.append((schema, validate(compiled)))
        except FileNotFoundError as exc:
            raise _StepError({"error": "'node' not found — Node.js must be installed."}) from exc
    return results


def _require_ajv() -> None:
    if not (_AJV_DIR / "node_modules").exists():
        raise _StepError({"error": f"node_modules not found. Run: npm install (in {_AJV_DIR})"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    data_dir      = os.environ.get("AAS_DATA_DIR", "")
    fmt           = os.environ.get("AAS_FORMAT", "json").lower()
    known_version = os.environ.get("AAS_VERSION")
    version_file  = os.environ.get("AAS_DETECTED_VERSION_FILE", "")

    if not data_dir:
        emit({"error": "AAS_DATA_DIR not set.", "format": fmt})
        return
    if fmt not in ("json", "xml", "aasx"):
        emit({"format": fmt, "error": "unsupported format"})
        return

    detected_version: str | None = None
    inner_fmt_label: str | None = None
    json_entries: list[tuple[Path, bytes]] = []
    xml_entries:  list[tuple[Path, bytes]] = []

    try:
        if fmt == "json":
            files = sorted(Path(data_dir).glob("*.json"))
            if not files:
                emit({"error": f"No JSON files found in {data_dir}", "format": fmt,
                      "directory": data_dir})
                return
            schemas, discovered = _select_schemas("json", known_version)
            if not schemas:
                _emit_schema_skip("json", fmt, discovered, known_version)
                return
            _require_ajv()
            results = _run_schemas(
                schemas, _load_schema, lambda s: _validate_against_schema(s, files)
            )
            detected_version, schema_results = _build_schema_output(results, known_version, "AJV")

        elif fmt == "xml":
            files = sorted(Path(data_dir).glob("*.xml"))
            if not files:
                emit({"error": f"No XML files found in {data_dir}", "format": fmt,
                      "directory": data_dir})
                return
            schemas, discovered = _select_schemas("xml", known_version)
            if not schemas:
                _emit_schema_skip("xml", fmt, discovered, known_version)
                return
            results = _run_schemas(
                schemas, _load_xsd, lambda s: _validate_xml_against_schema(s, files)
            )
            detected_version, schema_results = _build_schema_output(results, known_version, "XSD")

        else:  # aasx — unpack once, then validate the serialisation found inside
            aasx_files = sorted(Path(data_dir).glob("*.aasx"))
            if not aasx_files:
                emit({"error": f"No AASX files found in {data_dir}", "format": fmt,
                      "directory": data_dir})
                return

            for aasx_path in aasx_files:
                try:
                    with zipfile.ZipFile(aasx_path) as zf:
                        inner_fmt, data = _find_aasx_data(zf)
                except Exception:
                    continue
                if inner_fmt == "json":
                    json_entries.append((aasx_path, data))
                elif inner_fmt == "xml":
                    xml_entries.append((aasx_path, data))

            inner_fmt_label = "json" if len(json_entries) >= len(xml_entries) else "xml"
            schemas, discovered = _select_schemas(inner_fmt_label, known_version)
            if not schemas:
                _emit_schema_skip(inner_fmt_label, fmt, discovered, known_version)
                return
            if inner_fmt_label == "json":
                _require_ajv()
                results = _run_schemas(
                    schemas, _load_schema,
                    lambda s: _validate_json_bytes_against_schema(s, json_entries),
                )
                validator = "AJV"
            else:
                results = _run_schemas(
                    schemas, _load_xsd,
                    lambda s: _validate_xml_bytes_against_schema(s, xml_entries),
                )
                validator = "XSD"
            detected_version, schema_results = _build_schema_output(
                results, known_version, validator
            )
    except _StepError as err:
        emit(err.payload)
        return

    # Write detected version to handoff file for constraint_check.py
    if version_file:
        Path(version_file).write_text(detected_version or "", encoding="utf-8")

    out: dict = {
        "format": fmt,
        "directory": data_dir,
        "detected_version": detected_version,
        "known_version": known_version,
        "schema_results": schema_results,
    }
    if inner_fmt_label:
        out["aasx_inner_format"] = inner_fmt_label
        out["aasx_json_entries"] = len(json_entries)
        out["aasx_xml_entries"]  = len(xml_entries)

    emit(out)


if __name__ == "__main__":
    main()
