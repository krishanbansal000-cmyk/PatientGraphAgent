"""Minimal Neo4j provenance bridge between Graphiti memory and canonical FHIR.

Cloud Healthcare FHIR remains authoritative. Neo4j stores Graphiti's temporal
memory plus only enough source metadata to resolve every returned fact back to
the exact FHIR resources that supported its episode.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProvenanceConfig:
    uri: str
    username: str
    password: str
    database: str
    source_id: str

    @classmethod
    def from_environment(cls) -> Optional["ProvenanceConfig"]:
        uri = os.environ.get("NEO4J_URI", "").strip()
        username = os.environ.get("NEO4J_USERNAME", "").strip()
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not uri:
            return None
        if not username or not password:
            log.warning("NEO4J_URI is set but Neo4j credentials are incomplete")
            return None

        source_id = os.environ.get("FHIR_SOURCE_ID", "").strip() or "/".join(
            (
                os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app"),
                os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                os.environ.get("HEALTHCARE_DATASET", "myhealth-dataset"),
                os.environ.get("HEALTHCARE_FHIR_STORE", "myhealth-fhir-store"),
            )
        )
        return cls(
            uri=uri,
            username=username,
            password=password,
            database=os.environ.get("NEO4J_DATABASE", "neo4j").strip() or "neo4j",
            source_id=source_id,
        )


class FHIRProvenanceBridge:
    """Patient-isolated links from Graphiti episodes to compact FHIR sources."""

    _SCHEMA_QUERIES = (
        "CREATE CONSTRAINT fhir_source_graph_key IF NOT EXISTS "
        "FOR (n:FHIRSource) REQUIRE n.graph_key IS UNIQUE",
        "CREATE INDEX fhir_source_key IF NOT EXISTS "
        "FOR (n:FHIRSource) ON (n.fhir_key)",
    )

    def __init__(self, config: ProvenanceConfig):
        self.config = config
        self._driver = None
        self._schema_ready = False
        self._lock = threading.Lock()

    def _get_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self.config.uri,
                auth=(self.config.username, self.config.password),
                connection_timeout=8.0,
            )
        return self._driver

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            with self._get_driver().session(database=self.config.database) as session:
                for query in self._SCHEMA_QUERIES:
                    session.run(query).consume()
            self._schema_ready = True

    def link_episodes(
        self,
        patient_id: str,
        *,
        group_id: str,
        episodes: Sequence[Dict[str, Any]],
    ) -> int:
        """Replace one snapshot's episode-to-FHIR provenance links idempotently."""
        self._ensure_schema()
        bounded_episodes = list(episodes)[:500]
        if not bounded_episodes:
            return 0

        query = """
        UNWIND $episodes AS episode
        MATCH (memory:Episodic {uuid: episode.uuid, group_id: $group_id})
        SET memory.patient_id = $patient_id, memory.saga = episode.saga
        WITH memory, episode
        OPTIONAL MATCH (memory)-[old_derived:DERIVED_FROM]->(old_source:FHIRSource)
        WHERE old_derived.group_id = $group_id
          AND NOT old_source.fhir_key IN episode.fhir_keys
        DELETE old_derived
        WITH DISTINCT memory, episode
        UNWIND episode.sources AS source
        MERGE (fhir:FHIRSource {graph_key: source.graph_key})
        SET fhir += source
        MERGE (memory)-[derived:DERIVED_FROM]->(fhir)
        SET derived.patient_id = $patient_id, derived.group_id = $group_id
        RETURN count(DISTINCT memory) AS linked
        """
        with self._get_driver().session(database=self.config.database) as session:
            record = session.run(
                query,
                patient_id=patient_id,
                group_id=group_id,
                episodes=bounded_episodes,
            ).single()
        return int(record.get("linked") or 0) if record else 0

    def sources_for_episodes(
        self,
        patient_id: str,
        *,
        group_id: str,
        episode_uuids: Sequence[str],
        limit: int = 100,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Resolve active Graphiti episodes to patient-owned FHIR references."""
        self._ensure_schema()
        bounded_uuids = list(
            dict.fromkeys(str(value) for value in episode_uuids if value)
        )[:100]
        if not bounded_uuids:
            return {}

        query = """
        MATCH (memory:Episodic)-[derived:DERIVED_FROM]->(fhir:FHIRSource)
        WHERE memory.uuid IN $episode_uuids
          AND memory.group_id = $group_id
          AND memory.patient_id = $patient_id
          AND memory.avinia_active = true
          AND derived.group_id = $group_id
          AND derived.patient_id = $patient_id
        RETURN memory.uuid AS episode_uuid,
               fhir{.fhir_key, .resource_type, .resource_id, .fhir_version,
                    .last_updated, .source_id} AS source
        ORDER BY episode_uuid, source.fhir_key
        LIMIT $limit
        """
        with self._get_driver().session(database=self.config.database) as session:
            records = session.run(
                query,
                patient_id=patient_id,
                group_id=group_id,
                episode_uuids=bounded_uuids,
                limit=max(1, min(limit, 200)),
            )
            result: Dict[str, List[Dict[str, Any]]] = {}
            for record in records:
                source = record.get("source")
                if source:
                    result.setdefault(record["episode_uuid"], []).append(dict(source))
            return result

    def set_active_episodes(
        self,
        patient_id: str,
        *,
        group_id: str,
        episode_uuids: Sequence[str],
    ) -> int:
        """Activate the current patient memory snapshot and retain old revisions."""
        self._ensure_schema()
        bounded_uuids = list(
            dict.fromkeys(str(value) for value in episode_uuids if value)
        )[:500]
        with self._get_driver().session(database=self.config.database) as session:
            record = session.run(
                "MATCH (memory:Episodic {group_id: $group_id}) "
                "SET memory.avinia_active = memory.uuid IN $episode_uuids "
                "FOREACH (_ IN CASE WHEN memory.uuid IN $episode_uuids THEN [1] ELSE [] END | "
                "SET memory.patient_id = $patient_id) "
                "RETURN count(CASE WHEN memory.avinia_active THEN 1 END) AS active",
                patient_id=patient_id,
                group_id=group_id,
                episode_uuids=bounded_uuids,
            ).single()
        return int(record.get("active") or 0) if record else 0


_bridge: Optional[FHIRProvenanceBridge] = None
_bridge_lock = threading.Lock()


def get_fhir_provenance_bridge() -> Optional[FHIRProvenanceBridge]:
    """Return the lazy provenance bridge, or ``None`` when Neo4j is disabled."""
    global _bridge
    config = ProvenanceConfig.from_environment()
    if config is None:
        return None
    if _bridge is None:
        with _bridge_lock:
            if _bridge is None:
                _bridge = FHIRProvenanceBridge(config)
    return _bridge
