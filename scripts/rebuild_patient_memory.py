"""Synchronously rebuild one patient's Graphiti memory and FHIR provenance.

Use this POC command when completion must be known before the process exits.
It reloads canonical FHIR data and does not place patient data on a task queue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from clinical_core.patient_memory import get_patient_memory_service
from clinical_core.patient_journey import patient_group_id
from clinical_core.tools import load_patient_resources


def reset_patient_group(patient_id: str) -> dict[str, int]:
    """Delete only this patient's derived Graphiti partition before a clean rebuild."""
    uri = os.environ.get("NEO4J_URI", "")
    username = os.environ.get("NEO4J_USERNAME", "")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not uri or not username or not password:
        raise RuntimeError("Neo4j connection environment variables are required for --reset")

    group_id = patient_group_id(patient_id)
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
            record = session.run(
                "MATCH (node {group_id: $group_id}) RETURN count(node) AS count",
                group_id=group_id,
            ).single()
            removed_nodes = int(record["count"] or 0) if record else 0
            session.run(
                "MATCH (node {group_id: $group_id}) DETACH DELETE node",
                group_id=group_id,
            ).consume()
            record = session.run(
                "MATCH (source:FHIRSource) "
                "WHERE NOT (source)<-[:DERIVED_FROM]-(:Episodic) "
                "WITH collect(source) AS orphaned "
                "FOREACH (source IN orphaned | DETACH DELETE source) "
                "RETURN size(orphaned) AS count"
            ).single()
            removed_sources = int(record["count"] or 0) if record else 0
    finally:
        driver.close()
    return {"graph_nodes_removed": removed_nodes, "orphan_sources_removed": removed_sources}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronously rebuild the POC patient memory graph."
    )
    parser.add_argument(
        "--patient-id",
        default=os.environ.get("DEFAULT_PATIENT_ID", ""),
        help="FHIR Patient ID (defaults to DEFAULT_PATIENT_ID).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete this patient's derived graph partition before rebuilding it.",
    )
    args = parser.parse_args()
    patient_id = args.patient_id.strip()
    if not patient_id:
        parser.error("--patient-id or DEFAULT_PATIENT_ID is required")

    reset = reset_patient_group(patient_id) if args.reset else None
    resources, source = load_patient_resources(patient_id)
    status = get_patient_memory_service().sync_now(
        patient_id,
        resources,
        source=source,
    )
    if reset:
        status["reset"] = reset
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status.get("state") in {"succeeded", "disabled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
