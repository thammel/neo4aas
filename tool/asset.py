"""
Asset analysis.

Reports:
  1. assetKind distribution (Instance / Type / NotApplicable)
  2. specificAssetId key frequency ranking
  3. globalAssetId format classification (IRI, IRDI, UUID, Custom)

Reads environment variables (set by orchestrate.py):
  NEO4J_BOLT_URI  — bolt connection string (default: bolt://localhost:7687)
"""

import re
from collections import Counter

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient
from common import emit, make_client, pct

# ---------------------------------------------------------------------------
# globalAssetId format classification
# ---------------------------------------------------------------------------

_IRDI_RE = re.compile(r"^\d{4}[-/]")
_UUID_RE  = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def classify_global_asset_id(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return "IRI"
    if _IRDI_RE.match(value):
        return "IRDI"
    if _UUID_RE.match(value):
        return "UUID"
    return "Custom"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_asset_kinds(client: AASNeo4JClient):
    return client.execute_clause(
        """
        MATCH (a:AssetInformation)
        RETURN a.assetKind AS assetKind, count(a) AS count
        ORDER BY count DESC
        """
    )


def get_specific_asset_ids(client: AASNeo4JClient):
    return client.execute_clause(
        """
        MATCH (a:AssetInformation)-[:specificAssetId]->(id)
        RETURN id.name AS key, count(a) AS count
        ORDER BY count DESC
        """
    )


def get_global_asset_ids(client: AASNeo4JClient) -> list[str]:
    rows = client.execute_clause(
        "MATCH (a:AssetInformation) RETURN a.globalAssetId AS globalAssetId"
    )
    return [r["globalAssetId"] for r in rows if r["globalAssetId"]]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client = make_client()

    kinds = list(get_asset_kinds(client))
    total_kinds = sum(r["count"] for r in kinds)

    specific = list(get_specific_asset_ids(client))
    total_specific = sum(r["count"] for r in specific) if specific else 0

    global_ids = get_global_asset_ids(client)
    fmt_counter: Counter = Counter(classify_global_asset_id(v) for v in global_ids)
    total_global = len(global_ids)

    emit({
        "asset_kinds": [
            {"kind": r["assetKind"] or "None", "count": r["count"], "share_pct": pct(r["count"], total_kinds)}
            for r in kinds
        ],
        "specific_asset_ids": [
            {"key": r["key"] or "None", "count": r["count"], "share_pct": pct(r["count"], total_specific)}
            for r in specific
        ],
        "global_asset_id_formats": {
            "total": total_global,
            "distribution": [
                {"format": fmt, "count": cnt, "share_pct": pct(cnt, total_global)}
                for fmt, cnt in fmt_counter.most_common()
            ],
        },
    })


if __name__ == "__main__":
    main()
