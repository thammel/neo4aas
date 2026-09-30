"""
Shared utilities for all analysis scripts.

Credentials default to the docker-compose values but can be overridden:
  NEO4J_USER     — Neo4j username  (default: neo4j)
  NEO4J_PASSWORD — Neo4j password  (default: 12345678)
  NEO4J_BOLT_URI — Bolt connection string (default: bolt://localhost:7687)
"""

import json
import os
import sys

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG

NEO4J_USER     = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "12345678")


def make_client() -> AASNeo4JClient:
    uri = os.environ.get("NEO4J_BOLT_URI", "bolt://localhost:7687")
    return AASNeo4JClient(uri, NEO4J_USER, NEO4J_PASSWORD, AAS_NEO4J_MODEL_CONFIG)


def pct(part: int, total: int) -> float:
    return round(100.0 * part / total, 4) if total else 0.0


def emit(data: dict) -> None:
    """Write result dict as JSON to stdout. Call once per script."""
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
