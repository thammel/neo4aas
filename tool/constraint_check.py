"""
AAS constraint check — runs after schema_validation.py.

Reads the detected version from AAS_DETECTED_VERSION_FILE (written by
schema_validation.py) and runs AASConstraintChecker against the Neo4j graph.
orchestrate.py saves the result as constraint_check.json.

Reads environment variables (set by orchestrate.py):
  NEO4J_BOLT_URI            — bolt connection string (default: bolt://localhost:7687)
  AAS_VERSION               — known version; used when handoff file is absent
  AAS_DETECTED_VERSION_FILE — path written by schema_validation.py
"""

import logging
import os
from collections import defaultdict
from pathlib import Path

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

from aas_mapping.aas_neo4j_adapter.validation import AASConstraintChecker
from common import emit, make_client


def main() -> None:
    known_version = os.environ.get("AAS_VERSION")
    version_file  = os.environ.get("AAS_DETECTED_VERSION_FILE", "")

    # Prefer the version detected by schema_validation.py; fall back to known.
    # version_source records which one won: schema_validation.py skips itself when
    # the user supplied no schemas, and then nothing is detected. "unknown" means
    # AASConstraintChecker falls back to the V3.1 idShort pattern, which
    # understates AASd-002 on a V3.0 corpus — the numbers are not comparable.
    detected_version: str | None = None
    version_source = "unknown"
    if version_file and Path(version_file).exists():
        content = Path(version_file).read_text(encoding="utf-8").strip()
        detected_version = content or None
        if detected_version:
            version_source = "detected"
    if detected_version is None:
        detected_version = known_version
        if detected_version:
            version_source = "known"

    try:
        client = make_client()
        report = AASConstraintChecker(client, detected_version).check_all()

        by_constraint: dict[str, list] = defaultdict(list)
        for v in report.violations:
            by_constraint[v.constraint_id].append(v)

        violations_by_constraint = [
            {"constraint_id": cid, "count": len(vs), "example": vs[0].description}
            for cid, vs in sorted(by_constraint.items())
        ]

        emit({
            "version": detected_version,
            "version_source": version_source,
            "total_checked": len(report.checked_constraints),
            "checked_constraints": report.checked_constraints,
            "total_violations": len(report.violations),
            "violations_by_constraint": violations_by_constraint,
        })
    except Exception as exc:
        emit({"version": detected_version, "version_source": version_source, "error": str(exc)})


if __name__ == "__main__":
    main()
