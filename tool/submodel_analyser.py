"""
Submodel analysis.

Reports:
  1. Submodel type distribution by (idShort, semanticId), classified as
     IDTA template, Deprecated IDTA template, or Proprietary
  2. Number of submodels per AAS: min, max, mean, median, std

Reads environment variables (set by orchestrate.py):
  NEO4J_BOLT_URI               — bolt connection string (default: bolt://localhost:7687)
  AAS_IDTA_TEMPLATES_DIR       — directory of current IDTA submodel templates
                                 (default: idta-templates/ in the working directory)
  AAS_DEPRECATED_TEMPLATES_DIR — directory of withdrawn IDTA templates
                                 (default: deprecated-templates/ in the working
                                 directory)
"""

import json
import os
from collections import Counter
from pathlib import Path
from statistics import mean, median, stdev

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient
from common import emit, make_client, pct


def load_template_semantic_ids(template_dir: Path) -> set[str]:
    """Collect submodel semanticIds from the AAS Environment JSON files in *template_dir*.

    Both template directories sit at the repository root, next to data/ and
    schemas/, because they are user-supplied IDTA content like those are — not
    part of the tool. So they are resolved against the working directory. A
    missing directory is not an error: the origin classification degrades, the
    step still runs.
    """
    ids: set[str] = set()
    if not template_dir.exists():
        return ids
    for p in template_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for sm in data.get("submodels", []):
                # instances reference the template by its semanticId, not its id
                for key in (sm.get("semanticId") or {}).get("keys", []):
                    if val := key.get("value"):
                        ids.add(val)
        except Exception:
            pass
    return ids


def load_idta_semantic_ids() -> set[str]:
    return load_template_semantic_ids(
        Path(os.environ.get("AAS_IDTA_TEMPLATES_DIR") or "idta-templates")
    )


def load_deprecated_semantic_ids() -> set[str]:
    return load_template_semantic_ids(
        Path(os.environ.get("AAS_DEPRECATED_TEMPLATES_DIR") or "depricated-templates")
    )


def get_submodel_types(client: AASNeo4JClient) -> Counter:
    rows = client.execute_clause(
        """
        MATCH (s:Submodel)
        OPTIONAL MATCH (s)-[:semanticId]->(sem)
        RETURN s.idShort AS idShort, sem.keys_value[0] AS semanticId, count(s) AS count
        ORDER BY count DESC
        """
    )
    counter: Counter = Counter()
    for r in rows:
        key = (r["idShort"], r["semanticId"])
        counter[key] += r["count"]
    return counter


def get_submodels_per_aas(client: AASNeo4JClient) -> list[int]:
    rows = client.execute_clause(
        "MATCH (a:AssetAdministrationShell)-[:submodels]->(s) RETURN a.id AS id, count(s) AS count"
    )
    return [r["count"] for r in rows]


def main() -> None:
    client         = make_client()
    idta_ids       = load_idta_semantic_ids()
    deprecated_ids = load_deprecated_semantic_ids()

    sm_types = get_submodel_types(client)
    total_submodels = sum(sm_types.values())
    show_origin = bool(idta_ids or deprecated_ids)

    rows = []
    for (id_short, semantic_id), count in sm_types.most_common():
        row: dict = {
            "id_short": id_short,
            "semantic_id": semantic_id,
            "count": count,
            "share_pct": pct(count, total_submodels),
        }
        if show_origin:
            if semantic_id in idta_ids:
                origin = "IDTA"
            elif semantic_id in deprecated_ids:
                origin = "Deprecated"
            elif semantic_id:
                origin = "Proprietary"
            else:
                origin = None
            row["origin"] = origin
        rows.append(row)

    counts = get_submodels_per_aas(client)
    per_aas = {
        "n_shells": len(counts),
        "min": min(counts) if counts else None,
        "max": max(counts) if counts else None,
        "mean": round(mean(counts), 4) if counts else None,
        "median": median(counts) if counts else None,
        "std": round(stdev(counts) if len(counts) > 1 else 0.0, 4) if counts else None,
    }

    emit({
        "submodel_type_distribution": {
            "total": total_submodels,
            "idta_template_ids_available": bool(idta_ids),
            "deprecated_template_ids_available": bool(deprecated_ids),
            "distribution": rows,
        },
        "submodels_per_aas": per_aas,
    })


if __name__ == "__main__":
    main()
