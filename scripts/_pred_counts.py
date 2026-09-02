"""Print ontology_predicates counts for mapping QA. Not a pipeline step."""

from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "managraph.db")


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "counts"
    conn = sqlite3.connect(DB)
    print(f"=== {label} ===")
    print("forge_records", conn.execute("SELECT COUNT(*) FROM forge_records").fetchone()[0])
    print("ontology_cards", conn.execute("SELECT COUNT(*) FROM ontology_cards").fetchone()[0])
    print(
        "ontology_predicates",
        conn.execute("SELECT COUNT(*) FROM ontology_predicates").fetchone()[0],
    )
    print("predicates:")
    for row in conn.execute(
        "SELECT predicate, COUNT(*) FROM ontology_predicates GROUP BY predicate ORDER BY predicate"
    ):
        print(f"  {row[0]} {row[1]}")
    print("protects by target_class:")
    for row in conn.execute(
        "SELECT arg_value, COUNT(*) FROM ontology_predicates "
        "WHERE predicate='protects' AND arg_key='target_class' "
        "GROUP BY arg_value ORDER BY arg_value"
    ):
        print(f"  {row[0]} {row[1]}")
    print("requires by precondition:")
    for row in conn.execute(
        "SELECT arg_value, COUNT(*) FROM ontology_predicates "
        "WHERE predicate='requires' AND arg_key='precondition' "
        "GROUP BY arg_value ORDER BY arg_value"
    ):
        print(f"  {row[0]} {row[1]}")
    print("enables by capability:")
    for row in conn.execute(
        "SELECT arg_value, COUNT(*) FROM ontology_predicates "
        "WHERE predicate='enables' AND arg_key='capability' "
        "GROUP BY arg_value ORDER BY arg_value"
    ):
        print(f"  {row[0]} {row[1]}")
    print("tutors by selector:")
    for row in conn.execute(
        "SELECT arg_value, COUNT(*) FROM ontology_predicates "
        "WHERE predicate='tutors' AND arg_key='selector' "
        "GROUP BY arg_value ORDER BY arg_value"
    ):
        print(f"  {row[0]} {row[1]}")
    print("answers by threat_class:")
    for row in conn.execute(
        "SELECT arg_value, COUNT(*) FROM ontology_predicates "
        "WHERE predicate='answers' AND arg_key='threat_class' "
        "GROUP BY arg_value ORDER BY arg_value"
    ):
        print(f"  {row[0]} {row[1]}")
    print("rewards type looking like Card.:")
    print(
        conn.execute(
            "SELECT COUNT(*) FROM ontology_predicates "
            "WHERE predicate='rewards' AND arg_key='type' AND arg_value LIKE '%Card.%'"
        ).fetchone()[0]
    )
    print("rewards type with comma or Forge-ish DSL:")
    print(
        conn.execute(
            "SELECT COUNT(*) FROM ontology_predicates "
            "WHERE predicate='rewards' AND arg_key='type' "
            "AND (arg_value LIKE '%Card.%' OR arg_value LIKE '%,%' OR arg_value LIKE '%.%')"
        ).fetchone()[0]
    )
    print("rewards type top values:")
    for row in conn.execute(
        "SELECT arg_value, COUNT(*) FROM ontology_predicates "
        "WHERE predicate='rewards' AND arg_key='type' "
        "GROUP BY arg_value ORDER BY COUNT(*) DESC LIMIT 15"
    ):
        print(f"  {row[0]} {row[1]}")
    print("emits bounce:")
    print(
        conn.execute(
            "SELECT COUNT(*) FROM ontology_predicates "
            "WHERE predicate='emits' AND arg_key='event' AND arg_value='bounce'"
        ).fetchone()[0]
    )
    print("samples:")
    for title, sql in (
        (
            "protects commander",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='protects' AND arg_value='commander' "
            "ORDER BY card_name LIMIT 5",
        ),
        (
            "protects board",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='protects' AND arg_value='board' "
            "ORDER BY card_name LIMIT 5",
        ),
        (
            "haste_grant",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='enables' AND arg_value='haste_grant' "
            "ORDER BY card_name LIMIT 5",
        ),
        (
            "keyword_grant",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='enables' AND arg_value='keyword_grant' "
            "ORDER BY card_name LIMIT 5",
        ),
        (
            "convoke_like",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='enables' AND arg_value='convoke_like' "
            "ORDER BY card_name LIMIT 5",
        ),
        (
            "cost_reduction",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='enables' AND arg_value='cost_reduction' "
            "ORDER BY card_name LIMIT 5",
        ),
        (
            "requires",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='requires' ORDER BY card_name LIMIT 5",
        ),
        (
            "tutors any",
            "SELECT DISTINCT card_name FROM ontology_predicates "
            "WHERE predicate='tutors' AND arg_value='any' "
            "ORDER BY card_name LIMIT 5",
        ),
    ):
        cards = [row[0] for row in conn.execute(sql).fetchall()]
        print(f"  {title}: {', '.join(cards)}")
    print(
        "Lightning Greaves:",
        conn.execute(
            "SELECT predicate, arg_key, arg_value FROM ontology_predicates "
            "WHERE card_name='Lightning Greaves' AND predicate IN ('protects','enables')"
        ).fetchall(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
