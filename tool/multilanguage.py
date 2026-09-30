"""
Multilanguage usage analysis.

Reports:
  1. MultiLanguageProperty vs Property (xs:string only) counts and share
  2. BCP 47 language tag frequency across all multilingual text fields
     (MLP values, description, displayName)
  3. Share of Referable elements that populate description / displayName

Reads environment variables (set by orchestrate.py):
  NEO4J_BOLT_URI  — bolt connection string (default: bolt://localhost:7687)
"""

from collections import Counter

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient
from common import emit, make_client, pct


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_mlp_vs_property_counts(client: AASNeo4JClient) -> tuple[int, int]:
    mlp = client.execute_clause(
        "MATCH (n:MultiLanguageProperty) RETURN count(n) AS c", True
    )["c"]
    prop = client.execute_clause(
        "MATCH (n:Property) WHERE n.valueType = 'xs:string' RETURN count(n) AS c", True
    )["c"]
    return mlp, prop


def get_language_tag_frequencies(client: AASNeo4JClient) -> dict[str, Counter[str]]:
    """
    Collect BCP 47 language tags separately per multilingual text field:
      - mlp_value:    MultiLanguageProperty.value_language
      - description:  Referable.description_language
      - display_name: Referable.displayName_language
    """
    sources = {
        "mlp_value": (
            "MATCH (n:MultiLanguageProperty) WHERE n.value_language IS NOT NULL "
            "UNWIND n.value_language AS tag RETURN tag, count(*) AS cnt"
        ),
        "description": (
            "MATCH (n:Referable) WHERE n.description_language IS NOT NULL "
            "UNWIND n.description_language AS tag RETURN tag, count(*) AS cnt"
        ),
        "display_name": (
            "MATCH (n:Referable) WHERE n.displayName_language IS NOT NULL "
            "UNWIND n.displayName_language AS tag RETURN tag, count(*) AS cnt"
        ),
    }
    return {
        key: Counter({row["tag"]: row["cnt"] for row in client.execute_clause(q) if row["tag"]})
        for key, q in sources.items()
    }


def get_referable_annotation_coverage(client: AASNeo4JClient) -> tuple[int, int, int]:
    """Return (total_referables, with_description, with_display_name)."""
    row = client.execute_clause(
        """
        MATCH (n:Referable)
        RETURN count(n) AS total,
               count(n.description_language) AS with_desc,
               count(n.displayName_language) AS with_dn
        """,
        True,
    )
    return row["total"], row["with_desc"], row["with_dn"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client = make_client()

    mlp_count, prop_count = get_mlp_vs_property_counts(client)
    total_typed = mlp_count + prop_count

    lang_counters = get_language_tag_frequencies(client)

    total_ref, with_desc, with_dn = get_referable_annotation_coverage(client)

    def _dist(counter: Counter[str]) -> dict:
        total = sum(counter.values())
        return {
            "total_occurrences": total,
            "distribution": [
                {"tag": tag, "count": cnt, "share_pct": pct(cnt, total)}
                for tag, cnt in counter.most_common()
            ],
        }

    emit({
        "mlp_vs_property": {
            "MultiLanguageProperty": {"count": mlp_count, "share_pct": pct(mlp_count, total_typed)},
            "Property_string": {"count": prop_count, "share_pct": pct(prop_count, total_typed)},
        },
        "language_tag_frequencies": {
            "mlp_value":    _dist(lang_counters["mlp_value"]),
            "description":  _dist(lang_counters["description"]),
            "display_name": _dist(lang_counters["display_name"]),
        },
        "referable_annotation_coverage": {
            "total_referables": total_ref,
            "with_description": {"count": with_desc, "share_pct": pct(with_desc, total_ref)},
            "with_display_name": {"count": with_dn, "share_pct": pct(with_dn, total_ref)},
        },
    })


if __name__ == "__main__":
    main()
