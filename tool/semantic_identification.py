"""
Semantic identification analysis.

For every semanticId / supplementalSemanticId attached to a SubmodelElement
the script reports:
  1. ID-type distribution  — IRDI, IRI, or Custom
  2. Source distribution   — ECLASS, IEC CDD, IDTA, or Custom
     (orthogonal to ID-type: an IRI can come from any source)
  3. Coverage             — share of SubmodelElements that carry a semanticId /
                            supplementalSemanticId

Reads environment variables (set by orchestrate.py):
  NEO4J_BOLT_URI  — bolt connection string (default: bolt://localhost:7687)
"""

import re
from collections import Counter

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient
from common import emit, make_client, pct

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

# IRDI: starts with a numeric RAI block, uses '#' as separator
# Examples: 0173-1#02-AAO677#003, 0112/2///61987#ABN590#002
_IRDI_RE = re.compile(r"^\d{4}[-/]")


def classify_id_type(semantic_id: str) -> str:
    if semantic_id.startswith(("http://", "https://")):
        return "IRI"
    if _IRDI_RE.match(semantic_id):
        return "IRDI"
    return "Custom"


def classify_source(semantic_id: str) -> str:
    lower = semantic_id.lower()
    # ECLASS: IRDI RAI 0173 or any IRI with 'eclass'
    if semantic_id.startswith("0173") or "eclass" in lower:
        return "ECLASS"
    # IEC CDD: IRDI RAI 0112 or IRI from cdd.iec.ch
    if semantic_id.startswith("0112") or "cdd.iec.ch" in lower:
        return "IEC CDD"
    # IDTA: admin-shell.io namespace or explicit 'idta' marker
    if "admin-shell.io" in lower or "idta" in lower:
        return "IDTA"
    return "Custom"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_semantic_id_coverage(client: AASNeo4JClient) -> tuple[int, int, int, int]:
    """Return (elements_with_sem, total_elements, submodels_with_sem, total_submodels)."""
    el_with = client.execute_clause(
        "MATCH (n:SubmodelElement)-[:semanticId]->() RETURN count(DISTINCT n) AS c", True
    )["c"]
    el_total = client.execute_clause(
        "MATCH (n:SubmodelElement) RETURN count(n) AS c", True
    )["c"]
    sm_with = client.execute_clause(
        "MATCH (n:Submodel)-[:semanticId]->() RETURN count(DISTINCT n) AS c", True
    )["c"]
    sm_total = client.execute_clause(
        "MATCH (n:Submodel) RETURN count(n) AS c", True
    )["c"]
    return el_with, el_total, sm_with, sm_total


def get_supplemental_semantic_id_coverage(client: AASNeo4JClient) -> tuple[int, int, int, int]:
    """Return (elements_with_supp, total_elements, submodels_with_supp, total_submodels)."""
    el_with = client.execute_clause(
        "MATCH (n:SubmodelElement)-[:supplementalSemanticIds]->() RETURN count(DISTINCT n) AS c", True
    )["c"]
    el_total = client.execute_clause(
        "MATCH (n:SubmodelElement) RETURN count(n) AS c", True
    )["c"]
    sm_with = client.execute_clause(
        "MATCH (n:Submodel)-[:supplementalSemanticIds]->() RETURN count(DISTINCT n) AS c", True
    )["c"]
    sm_total = client.execute_clause(
        "MATCH (n:Submodel) RETURN count(n) AS c", True
    )["c"]
    return el_with, el_total, sm_with, sm_total


def get_all_semantic_ids(client: AASNeo4JClient) -> Counter:
    """Return a Counter of {semanticId: occurrence_count} for SubmodelElements."""
    rows = client.execute_clause(
        """
        MATCH (n:SubmodelElement)-[:semanticId]->(sem)
        RETURN sem.keys_value[0] AS semanticId, count(*) AS cnt
        """
    )
    counter: Counter = Counter()
    for row in rows:
        sid = row["semanticId"]
        if sid:
            counter[sid] += row["cnt"]
    return counter


def get_all_supplemental_semantic_ids(client: AASNeo4JClient) -> Counter:
    """Return a Counter of {supplementalSemanticId: occurrence_count} for SubmodelElements."""
    rows = client.execute_clause(
        """
        MATCH (n:SubmodelElement)-[:supplementalSemanticIds]->(sem)
        RETURN sem.keys_value[0] AS semanticId, count(*) AS cnt
        """
    )
    counter: Counter = Counter()
    for row in rows:
        sid = row["semanticId"]
        if sid:
            counter[sid] += row["cnt"]
    return counter


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_distributions(counter: Counter) -> tuple[Counter, Counter, int]:
    id_type_counter: Counter = Counter()
    source_counter:  Counter = Counter()
    for sid, cnt in counter.items():
        id_type_counter[classify_id_type(sid)] += cnt
        source_counter[classify_source(sid)]   += cnt
    return id_type_counter, source_counter, sum(counter.values())


def main() -> None:
    client = make_client()

    el_with, el_total, sm_with, sm_total = get_semantic_id_coverage(client)
    supp_el_with, _, supp_sm_with, _ = get_supplemental_semantic_id_coverage(client)

    sem_counter  = get_all_semantic_ids(client)
    supp_counter = get_all_supplemental_semantic_ids(client)

    id_type_counter, source_counter, total_occurrences = _build_distributions(sem_counter)
    supp_id_type_counter, supp_source_counter, supp_total = _build_distributions(supp_counter)

    emit({
        "semantic_id": {
            "coverage": {
                "Submodel": {"with_semantic_id": sm_with, "total": sm_total, "share_pct": pct(sm_with, sm_total)},
                "SubmodelElement": {"with_semantic_id": el_with, "total": el_total, "share_pct": pct(el_with, el_total)},
            },
            "id_type_distribution": {
                "total_occurrences": total_occurrences,
                "distribution": [
                    {"type": t, "count": cnt, "share_pct": pct(cnt, total_occurrences)}
                    for t, cnt in id_type_counter.most_common()
                ],
            },
            "source_distribution": {
                "total_occurrences": total_occurrences,
                "distribution": [
                    {"source": s, "count": cnt, "share_pct": pct(cnt, total_occurrences)}
                    for s, cnt in source_counter.most_common()
                ],
            },
            "all_semantic_ids": [
                {"semantic_id": sid, "count": cnt, "type": classify_id_type(sid), "source": classify_source(sid)}
                for sid, cnt in sem_counter.most_common()
            ],
        },
        "supplemental_semantic_id": {
            "coverage": {
                "Submodel": {"with_supplemental_semantic_id": supp_sm_with, "total": sm_total, "share_pct": pct(supp_sm_with, sm_total)},
                "SubmodelElement": {"with_supplemental_semantic_id": supp_el_with, "total": el_total, "share_pct": pct(supp_el_with, el_total)},
            },
            "id_type_distribution": {
                "total_occurrences": supp_total,
                "distribution": [
                    {"type": t, "count": cnt, "share_pct": pct(cnt, supp_total)}
                    for t, cnt in supp_id_type_counter.most_common()
                ],
            },
            "source_distribution": {
                "total_occurrences": supp_total,
                "distribution": [
                    {"source": s, "count": cnt, "share_pct": pct(cnt, supp_total)}
                    for s, cnt in supp_source_counter.most_common()
                ],
            },
            "all_supplemental_semantic_ids": [
                {"semantic_id": sid, "count": cnt, "type": classify_id_type(sid), "source": classify_source(sid)}
                for sid, cnt in supp_counter.most_common()
            ],
        },
    })


if __name__ == "__main__":
    main()
