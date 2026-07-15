"""Remove the obsolete deterministic FHIR projection from the Aura graph.

The command is dry-run by default. It retains Graphiti nodes, facts, episode
maps, FHIRSource nodes, and episode provenance relationships.
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase


load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the cleanup. Without this flag only counts are shown.",
    )
    args = parser.parse_args()

    uri = os.environ.get("NEO4J_URI", "")
    username = os.environ.get("NEO4J_USERNAME", "")
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    if not uri or not username or not password:
        raise RuntimeError("Neo4j connection environment variables are required")

    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database) as session:
            counts = session.run(
                "MATCH (n) WHERE n:ClinicalEvent OR n:ClinicalConcept OR n:Patient "
                "RETURN count(n) AS legacy_nodes"
            ).single()
            print(f"Legacy nodes: {int(counts['legacy_nodes'] or 0)}")
            if not args.apply:
                print("Dry run only. Re-run with --apply to remove them.")
                return 0

            session.run(
                "MATCH (n) WHERE n:ClinicalEvent OR n:ClinicalConcept OR n:Patient "
                "DETACH DELETE n"
            ).consume()
            session.run("MATCH (source:FHIRSource) REMOVE source.patient_id").consume()

            for constraint in (
                "patient_graph_key",
                "event_graph_key",
                "concept_key",
            ):
                session.run(f"DROP CONSTRAINT {constraint} IF EXISTS").consume()
            for index in (
                "event_patient",
                "event_time",
                "fhir_source_patient",
            ):
                session.run(f"DROP INDEX {index} IF EXISTS").consume()

            remaining = session.run(
                "MATCH (n) WHERE n:ClinicalEvent OR n:ClinicalConcept OR n:Patient "
                "RETURN count(n) AS legacy_nodes"
            ).single()
            print(f"Legacy nodes after cleanup: {int(remaining['legacy_nodes'] or 0)}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
