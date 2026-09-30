"""
Statistical overview.

Reports:
  1. Total node counts: AssetAdministrationShell, Submodel, SubmodelElement
  2. SubmodelElement nesting depth: min, max, mean, median, std

Depth is defined as the path length from a Submodel root through
submodelElements and subsequent value edges to a leaf element.

Reads environment variables (set by orchestrate.py):
  NEO4J_BOLT_URI  — bolt connection string (default: bolt://localhost:7687)
"""

from statistics import mean, median, stdev

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient
from common import emit, make_client


def get_node_counts(client: AASNeo4JClient) -> tuple[int, int, int]:
    aas = client.execute_clause("MATCH (n:AssetAdministrationShell) RETURN count(n) AS c", True)["c"]
    sm  = client.execute_clause("MATCH (n:Submodel) RETURN count(n) AS c", True)["c"]
    sme = client.execute_clause("MATCH (n:SubmodelElement) RETURN count(n) AS c", True)["c"]
    return aas, sm, sme


def get_depth_distribution(client: AASNeo4JClient) -> list[int]:
    """
    Return one depth value per leaf SubmodelElement, where depth is the
    number of hops from the Submodel node to the leaf via submodelElements
    then value edges.
    """
    rows = client.execute_clause(
        """
        MATCH path = (s:Submodel)-[:submodelElements]->(first)-[:value*0..]->(leaf)
        WHERE NOT (leaf)-[:value]->()
        RETURN length(path) AS depth
        """
    )
    return [r["depth"] for r in rows]


def main() -> None:
    client = make_client()

    aas_count, sm_count, sme_count = get_node_counts(client)
    depths = get_depth_distribution(client)

    depth_stats = None
    if depths:
        depth_stats = {
            "n": len(depths),
            "min": min(depths),
            "max": max(depths),
            "mean": round(mean(depths), 4),
            "median": median(depths),
            "std": round(stdev(depths) if len(depths) > 1 else 0.0, 4),
        }

    emit({
        "node_counts": {
            "AssetAdministrationShell": aas_count,
            "Submodel": sm_count,
            "SubmodelElement": sme_count,
        },
        "nesting_depth": depth_stats,
    })


if __name__ == "__main__":
    main()
