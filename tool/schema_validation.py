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

No schemas are shipped with this repository — they are IDTA's to distribute.
Drop your own copies into schemas/json/ (3-0.json, 3-1.json) and schemas/xml/
(3-0.xsd, 3-1.xsd), or point AAS_SCHEMAS_DIR at a directory laid out that way.
When a needed schema is absent this step reports itself as skipped and the
pipeline continues; see the note on constraint_check.py in README.md.
"""

import io
import json
import logging
import os
import zipfile
from collections import Counter
from pathlib import Path

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

# The schema files the user is expected to supply, per serialisation and version.
_EXPECTED_SCHEMAS = {
    "json": {"3.0": "3-0.json", "3.1": "3-1.json"},
    "xml":  {"3.0": "3-0.xsd",  "3.1": "3-1.xsd"},
}


def _schema_path(kind: str, version: str) -> Path:
    root = _JSON_SCHEMAS_DIR if kind == "json" else _XML_SCHEMAS_DIR
    return root / _EXPECTED_SCHEMAS[kind][version]


def _missing_schemas(kind: str, versions: list[str]) -> list[str]:
    """Paths of the schemas needed for *versions* that are not on disk."""
    return [
        str(_schema_path(kind, v))
        for v in versions
        if v in _EXPECTED_SCHEMAS[kind] and not _schema_path(kind, v).is_file()
    ]


def _emit_schema_skip(kind: str, fmt: str, missing: list[str], known_version: str | None) -> None:
    """Report the step as skipped rather than failed.

    The AAS_DETECTED_VERSION_FILE handoff is deliberately *not* written: nothing
    was detected. constraint_check.py then falls back to AAS_VERSION and labels
    its output version_source="known". With AAS_VERSION unset too it falls back
    further to the V3.1 idShort pattern, which understates AASd-002 on a V3.0
    corpus — that case surfaces as version_source="unknown".
    """
    emit({
        "skipped": True,
        "reason": f"no {kind} schemas in {_SCHEMAS_DIR}",
        "schemas_dir": str(_SCHEMAS_DIR),
        "missing_schemas": missing,
        "format": fmt,
        "known_version": known_version,
        "detected_version": known_version,
    })


def _load_schema(filename: str) -> dict:
    return json.loads((_JSON_SCHEMAS_DIR / filename).read_text(encoding="utf-8"))


def _load_xsd(filename: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(_XML_SCHEMAS_DIR / filename)))


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

def _build_schema_output(
    results: dict[str, tuple[int, int, Counter]],
    known_version: str | None,
    violation_label: str,
) -> tuple[str | None, list[dict]]:
    """Return (effective_version, schema_results list for JSON output)."""
    if not results:
        return None, []

    if known_version and known_version in results:
        effective_version = known_version
        version_source = "known"
    else:
        effective_version = min(results, key=lambda v: (sum(results[v][2].values()), v))
        version_source = "detected"

    schema_results = []
    for label in sorted(results):
        valid, invalid, counter = results[label]
        total = valid + invalid
        schema_results.append({
            "schema_label": f"AAS {label} {violation_label.upper()}",
            "version": label,
            "valid_count": valid,
            "invalid_count": invalid,
            "valid_pct": pct(valid, total),
            "total_violations": sum(counter.values()),
            "violations": [{"type": vtype, "count": cnt} for vtype, cnt in counter.most_common()],
        })

    return effective_version, schema_results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    data_dir      = os.environ.get("AAS_DATA_DIR", "")
    fmt           = os.environ.get("AAS_FORMAT", "json").lower()
    known_version = os.environ.get("AAS_VERSION")
    version_file  = os.environ.get("AAS_DETECTED_VERSION_FILE", "")

    versions_to_check = [known_version] if known_version else ["3.0", "3.1"]
    detected_version: str | None = None
    results: dict[str, tuple[int, int, Counter]] = {}
    inner_fmt_label: str | None = None

    if fmt == "json":
        if not data_dir:
            emit({"error": "AAS_DATA_DIR not set.", "format": fmt})
            return
        files = sorted(Path(data_dir).glob("*.json"))
        if not files:
            emit({"error": f"No JSON files found in {data_dir}", "format": fmt, "directory": data_dir})
            return
        missing = _missing_schemas("json", versions_to_check)
        if missing:
            _emit_schema_skip("json", fmt, missing, known_version)
            return
        node_modules = _AJV_DIR / "node_modules"
        if not node_modules.exists():
            emit({"error": f"node_modules not found. Run: npm install (in {_AJV_DIR})"})
            return
        try:
            all_schemas = {"3.0": _load_schema("3-0.json"), "3.1": _load_schema("3-1.json")}
        except Exception as exc:
            emit({"error": f"could not load schemas: {exc}"})
            return
        for label in versions_to_check:
            try:
                valid, invalid, counter = _validate_against_schema(all_schemas[label], files)
            except FileNotFoundError:
                emit({"error": "'node' not found — Node.js must be installed."})
                return
            results[label] = (valid, invalid, counter)
        detected_version, schema_results = _build_schema_output(results, known_version, "AJV")

    elif fmt == "xml":
        if not data_dir:
            emit({"error": "AAS_DATA_DIR not set.", "format": fmt})
            return
        files = sorted(Path(data_dir).glob("*.xml"))
        if not files:
            emit({"error": f"No XML files found in {data_dir}", "format": fmt, "directory": data_dir})
            return
        missing = _missing_schemas("xml", versions_to_check)
        if missing:
            _emit_schema_skip("xml", fmt, missing, known_version)
            return
        try:
            all_xsds = {"3.0": _load_xsd("3-0.xsd"), "3.1": _load_xsd("3-1.xsd")}
        except Exception as exc:
            emit({"error": f"could not load XSD schemas: {exc}"})
            return
        for label in versions_to_check:
            valid, invalid, counter = _validate_xml_against_schema(all_xsds[label], files)
            results[label] = (valid, invalid, counter)
        detected_version, schema_results = _build_schema_output(results, known_version, "XSD")

    elif fmt == "aasx":
        if not data_dir:
            emit({"error": "AAS_DATA_DIR not set.", "format": fmt})
            return
        aasx_files = sorted(Path(data_dir).glob("*.aasx"))
        if not aasx_files:
            emit({"error": f"No AASX files found in {data_dir}", "format": fmt, "directory": data_dir})
            return

        json_entries: list[tuple[Path, bytes]] = []
        xml_entries:  list[tuple[Path, bytes]] = []
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

        if inner_fmt_label == "json":
            missing = _missing_schemas("json", versions_to_check)
            if missing:
                _emit_schema_skip("json", fmt, missing, known_version)
                return
            node_modules = _AJV_DIR / "node_modules"
            if not node_modules.exists():
                emit({"error": f"node_modules not found. Run: npm install (in {_AJV_DIR})"})
                return
            try:
                all_schemas = {"3.0": _load_schema("3-0.json"), "3.1": _load_schema("3-1.json")}
            except Exception as exc:
                emit({"error": f"could not load schemas: {exc}"})
                return
            for label in versions_to_check:
                try:
                    valid, invalid, counter = _validate_json_bytes_against_schema(all_schemas[label], json_entries)
                except FileNotFoundError:
                    emit({"error": "'node' not found — Node.js must be installed."})
                    return
                results[label] = (valid, invalid, counter)
            detected_version, schema_results = _build_schema_output(results, known_version, "AJV")
        else:
            missing = _missing_schemas("xml", versions_to_check)
            if missing:
                _emit_schema_skip("xml", fmt, missing, known_version)
                return
            try:
                all_xsds = {"3.0": _load_xsd("3-0.xsd"), "3.1": _load_xsd("3-1.xsd")}
            except Exception as exc:
                emit({"error": f"could not load XSD schemas: {exc}"})
                return
            for label in versions_to_check:
                valid, invalid, counter = _validate_xml_bytes_against_schema(all_xsds[label], xml_entries)
                results[label] = (valid, invalid, counter)
            detected_version, schema_results = _build_schema_output(results, known_version, "XSD")

    else:
        emit({"format": fmt, "error": "unsupported format"})
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
