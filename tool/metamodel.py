"""
Metamodel element frequency analysis.

Reports:
  1. SubmodelElement subtype distribution (ranked by frequency)
  2. valueType distribution (for elements that carry one)
  3. Qualifier and Extension attachment frequency at Submodel and SubmodelElement level
  4. embeddedDataSpecification presence

Reads environment variables (set by orchestrate.py):
  NEO4J_BOLT_URI  — bolt connection string (default: bolt://localhost:7687)
"""

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient
from common import emit, make_client, pct


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_submodel_element_types(client: AASNeo4JClient):
    return client.execute_clause(
        """
        MATCH (s:SubmodelElement)
        RETURN s.modelType AS modelType, count(s) AS count
        ORDER BY count DESC
        """
    )


def get_value_types(client: AASNeo4JClient):
    return client.execute_clause(
        """
        MATCH (s:SubmodelElement)
        WHERE s.valueType IS NOT NULL
        RETURN s.valueType AS valueType, count(s) AS count
        ORDER BY count DESC
        """
    )


def get_optional_feature_counts(client: AASNeo4JClient) -> dict[str, int]:
    """
    Count Qualifier and Extension attachments at Submodel and SubmodelElement
    level, and embeddedDataSpecification entries on any node.
    """
    return {
        "submodel_qualifier":            client.execute_clause("MATCH (:Submodel)-[:qualifiers]->(:Qualifier) RETURN count(*) AS c", True)["c"],
        "submodel_extension":            client.execute_clause("MATCH (:Submodel)-[:extensions]->(:Extension) RETURN count(*) AS c", True)["c"],
        "submodel_element_qualifier":    client.execute_clause("MATCH (:SubmodelElement)-[:qualifiers]->(:Qualifier) RETURN count(*) AS c", True)["c"],
        "submodel_element_extension":    client.execute_clause("MATCH (:SubmodelElement)-[:extensions]->(:Extension) RETURN count(*) AS c", True)["c"],
        "embedded_data_specification":   client.execute_clause("MATCH ()-[:embeddedDataSpecifications]->() RETURN count(*) AS c", True)["c"],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client = make_client()

    types = list(get_submodel_element_types(client))
    total_elements = sum(r["count"] for r in types)

    vtypes = list(get_value_types(client))
    total_typed = sum(r["count"] for r in vtypes)

    features = get_optional_feature_counts(client)

    emit({
        "submodel_element_types": {
            "total": total_elements,
            "distribution": [
                {"type": r["modelType"] or "None", "count": r["count"], "share_pct": pct(r["count"], total_elements)}
                for r in types
            ],
        },
        "value_types": {
            "total_with_value_type": total_typed,
            "distribution": [
                {"value_type": r["valueType"] or "None", "count": r["count"], "share_pct": pct(r["count"], total_typed)}
                for r in vtypes
            ],
        },
        "optional_features": features,
    })


if __name__ == "__main__":
    main()
