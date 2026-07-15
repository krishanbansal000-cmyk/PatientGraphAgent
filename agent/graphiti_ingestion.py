"""Deterministic conversion and sequential ingestion of patient journey episodes."""

from __future__ import annotations

import hashlib
import json
import uuid as uuid_lib
from collections import Counter
from datetime import datetime, timezone
from typing import Any, List, Sequence

from pydantic import BaseModel, Field

from agent.clinical_graph_schema import (
    CLINICAL_EXTRACTION_INSTRUCTIONS,
    EDGE_TYPE_MAP,
    EDGE_TYPES,
    ENTITY_TYPES,
)
from agent.patient_journey import JourneyEpisode, PatientJourney


PATIENT_JOURNEY_SAGA = "patient_clinical_journey"
EPISODE_UUID_NAMESPACE = uuid_lib.UUID("68473f0f-4406-4fbe-b51d-f021ee6a1a9b")
EPISODE_MAP_LEASE_SECONDS = 1800
MAX_MEMORY_ITEMS_PER_EPISODE = 6


class ClinicalMemoryEpisode(BaseModel):
    """Graphiti-ready episode plus exact FHIR references kept outside the LLM graph."""

    logical_id: str
    content_hash: str
    name: str
    body: str
    source_description: str
    reference_time: datetime
    group_id: str
    saga: str = PATIENT_JOURNEY_SAGA
    fhir_references: List[str] = Field(default_factory=list)


class MemoryIngestionResult(BaseModel):
    configured: bool
    attempted: int = 0
    ingested: int = 0
    reused: int = 0
    skipped: int = 0
    episode_uuids: List[str] = Field(default_factory=list)
    episode_links: List["MemoryEpisodeLink"] = Field(default_factory=list)
    invalid_entities_removed: int = 0
    invalid_edges_removed: int = 0
    unsafe_result_edges_removed: int = 0
    error: str | None = None


class MemoryEpisodeLink(BaseModel):
    logical_id: str
    content_hash: str
    graphiti_uuid: str
    fhir_references: List[str] = Field(default_factory=list)


def build_memory_episodes(
    journey: PatientJourney,
    *,
    include_undated: bool = False,
) -> List[ClinicalMemoryEpisode]:
    """Convert a canonical journey to dated, stable, oldest-first memory episodes."""
    converted: List[ClinicalMemoryEpisode] = []
    for journey_episode in journey.episodes:
        for episode in _split_dense_episode(journey_episode):
            reference_time = _parse_datetime(episode.date)
            clinical_date_unknown = reference_time is None
            if reference_time is None and not include_undated:
                continue
            if reference_time is None:
                reference_time = _parse_datetime(journey.generated_at) or datetime.now(timezone.utc)
            converted.append(
                journey_episode_to_memory_episode(
                    journey,
                    episode,
                    reference_time=reference_time,
                    clinical_date_unknown=clinical_date_unknown,
                )
            )
    converted.sort(key=lambda item: (item.reference_time, item.logical_id))
    return converted


def _split_dense_episode(episode: JourneyEpisode) -> List[JourneyEpisode]:
    """Bound extraction size while retaining the visit anchor in each chunk."""
    if len(episode.items) <= MAX_MEMORY_ITEMS_PER_EPISODE:
        return [episode]

    visit_items = [item for item in episode.items if item.category == "visit"][:1]
    clinical_items = [item for item in episode.items if item not in visit_items]
    chunk_size = max(1, MAX_MEMORY_ITEMS_PER_EPISODE - len(visit_items))
    chunks = [
        clinical_items[index : index + chunk_size]
        for index in range(0, len(clinical_items), chunk_size)
    ]
    citations_by_reference = {
        citation.reference: citation for citation in episode.citations
    }
    parts: List[JourneyEpisode] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        items = [*visit_items, *chunk]
        references = {item.reference for item in items}
        parts.append(
            episode.model_copy(
                deep=True,
                update={
                    "id": f"{episode.id}:memory-part:{index}-of-{total}",
                    "title": f"{episode.title} (part {index} of {total})",
                    "summary": f"{episode.summary} Memory segment {index} of {total}.",
                    "items": items,
                    "citations": [
                        citations_by_reference[reference]
                        for reference in sorted(references)
                        if reference in citations_by_reference
                    ],
                    "category_counts": dict(Counter(item.category for item in items)),
                },
            )
        )
    return parts


def journey_episode_to_memory_episode(
    journey: PatientJourney,
    episode: JourneyEpisode,
    *,
    reference_time: datetime | None = None,
    clinical_date_unknown: bool = False,
) -> ClinicalMemoryEpisode:
    """Create a stable Graphiti input without putting the raw FHIR bundle in Neo4j."""
    resolved_time = reference_time or _parse_datetime(episode.date)
    if resolved_time is None:
        raise ValueError("A journey episode needs a clinical date before memory ingestion")

    fhir_references = sorted({citation.reference for citation in episode.citations})
    payload = {
        "schema_version": "clinical-memory-v2",
        "subject": {
            "entity_type": "PatientRecordSubject",
            "stable_key": journey.group_id,
            "display": "this patient",
        },
        "episode": {
            "journey_episode_id": episode.id,
            "type": episode.type,
            "title": episode.title,
            "summary": episode.summary,
            "status": episode.status,
            "occurred_at": episode.date,
            "ended_at": episode.end_date,
            "clinical_date_unknown": clinical_date_unknown,
            "encounter_fhir_reference": (
                f"Encounter/{episode.encounter_id}" if episode.encounter_id else None
            ),
        },
        "clinical_items": [_memory_item_payload(item) for item in episode.items],
        "fhir_sources": [citation.model_dump(mode="json") for citation in episode.citations],
    }
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    fingerprint_body = json.dumps(
        {
            "memory_payload": payload,
            "source_items": [item.model_dump(mode="json") for item in episode.items],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    logical_id = str(
        uuid_lib.uuid5(
            EPISODE_UUID_NAMESPACE,
            f"{journey.group_id}:{PATIENT_JOURNEY_SAGA}:{episode.id}",
        )
    )
    content_hash = hashlib.sha256(fingerprint_body.encode("utf-8")).hexdigest()
    date_label = episode.date[:10] if episode.date else "undated"
    name = (
        f"FHIR journey {logical_id[:12]} revision {content_hash[:12]} | "
        f"{date_label} | {episode.title}"
    )[:240]
    return ClinicalMemoryEpisode(
        logical_id=logical_id,
        content_hash=content_hash,
        name=name,
        body=body,
        source_description="Canonical FHIR patient journey episode",
        reference_time=resolved_time,
        group_id=journey.group_id,
        fhir_references=fhir_references,
    )


def _memory_item_payload(item: Any) -> dict[str, Any]:
    """Keep semantic identity in Graphiti while exact result values remain in FHIR."""
    payload = {
        "fhir_reference": item.reference,
        "resource_type": item.resource_type,
        "category": item.category,
        "display": item.display,
        "status": item.status,
        "clinical_date": item.date,
        "terminology_code": item.code,
    }
    if item.category != "result":
        payload["summary"] = item.summary
    return payload


async def ingest_memory_episodes(
    client: Any,
    episodes: Sequence[ClinicalMemoryEpisode],
) -> MemoryIngestionResult:
    """Ingest episodes sequentially so the Saga chain has deterministic order."""
    if client is None:
        return MemoryIngestionResult(configured=False, skipped=len(episodes))

    ordered = sorted(episodes, key=lambda item: (item.reference_time, item.logical_id))
    result = MemoryIngestionResult(configured=True, attempted=len(ordered))
    previous_episode_uuid: str | None = None
    active_episode: ClinicalMemoryEpisode | None = None
    active_owner_token: str | None = None

    try:
        # Idempotent index creation only. This intentionally never calls clear_data.
        await client.build_indices_and_constraints()
        await _ensure_episode_map_constraint(client)
        from graphiti_core.nodes import EpisodeType

        for episode in ordered:
            owner_token = str(uuid_lib.uuid4())
            reservation = await _reserve_episode_map(client, episode, owner_token)
            active_episode = episode
            active_owner_token = owner_token
            actual_uuid = reservation.get("graphiti_uuid")
            if actual_uuid and reservation.get("status") == "complete":
                link = _episode_link(episode, str(actual_uuid))
                previous_episode_uuid = link.graphiti_uuid
                result.episode_uuids.append(link.graphiti_uuid)
                result.episode_links.append(link)
                result.ingested += 1
                result.reused += 1
                active_episode = None
                active_owner_token = None
                continue

            if reservation.get("owner_token") != owner_token:
                raise RuntimeError(
                    f"Memory episode {episode.logical_id} is currently being ingested by another worker"
                )

            # Recover a prior Graphiti write if a worker stopped before completing
            # the application-owned map. The name includes logical id + content hash.
            actual_uuid = await _find_graphiti_episode_uuid(client, episode)
            if not actual_uuid:
                added = await client.add_episode(
                    name=episode.name,
                    episode_body=episode.body,
                    source_description=episode.source_description,
                    reference_time=episode.reference_time,
                    source=EpisodeType.json,
                    group_id=episode.group_id,
                    update_communities=False,
                    entity_types=ENTITY_TYPES,
                    edge_types=EDGE_TYPES,
                    edge_type_map=EDGE_TYPE_MAP,
                    custom_extraction_instructions=CLINICAL_EXTRACTION_INSTRUCTIONS,
                    saga=episode.saga,
                    saga_previous_episode_uuid=previous_episode_uuid,
                )
                actual_uuid = str(getattr(getattr(added, "episode", None), "uuid", ""))
                if not actual_uuid:
                    raise RuntimeError("Graphiti did not return the created episode UUID")

            stored_uuid = await _complete_episode_map(
                client,
                episode,
                owner_token=owner_token,
                graphiti_uuid=str(actual_uuid),
            )
            link = _episode_link(episode, stored_uuid)
            previous_episode_uuid = stored_uuid
            result.episode_uuids.append(stored_uuid)
            result.episode_links.append(link)
            result.ingested += 1
            active_episode = None
            active_owner_token = None
        for group_id in dict.fromkeys(episode.group_id for episode in ordered):
            sanitation = await _sanitize_group_semantics(client, group_id)
            result.invalid_entities_removed += sanitation["invalid_entities_removed"]
            result.invalid_edges_removed += sanitation["invalid_edges_removed"]
            result.unsafe_result_edges_removed += sanitation["unsafe_result_edges_removed"]
    except Exception as exc:  # The background status must report partial ingestion safely.
        if active_episode is not None and active_owner_token is not None:
            try:
                await _mark_episode_map_failed(client, active_episode, active_owner_token)
            except Exception:
                pass
        result.error = f"{type(exc).__name__}: {exc}"
        result.skipped = len(ordered) - result.ingested
        return result

    return result


async def _sanitize_group_semantics(client: Any, group_id: str) -> dict[str, int]:
    """Remove model output outside the POC schema before it can be retrieved."""
    resource_rules = [
        {"prefix": "Encounter/", "label": "ClinicalEncounter"},
        {"prefix": "Condition/", "label": "ClinicalCondition"},
        {"prefix": "MedicationRequest/", "label": "MedicationTherapy"},
        {"prefix": "MedicationStatement/", "label": "MedicationTherapy"},
        {"prefix": "MedicationDispense/", "label": "MedicationTherapy"},
        {"prefix": "MedicationAdministration/", "label": "MedicationTherapy"},
        {"prefix": "Observation/", "label": "ClinicalObservation"},
        {"prefix": "DiagnosticReport/", "label": "ClinicalObservation"},
        {"prefix": "AllergyIntolerance/", "label": "ClinicalAllergy"},
        {"prefix": "Procedure/", "label": "ClinicalProcedure"},
        {"prefix": "Immunization/", "label": "ClinicalImmunization"},
        {"prefix": "CarePlan/", "label": "ClinicalCarePlan"},
        {"prefix": "Goal/", "label": "ClinicalCarePlan"},
    ]
    edge_rules = [
        {"source": source, "target": target, "name": edge_name}
        for (source, target), edge_names in EDGE_TYPE_MAP.items()
        for edge_name in edge_names
    ]
    records, _, _ = await client.driver.execute_query(
        """
        MATCH (entity:Entity {group_id: $group_id})
        WHERE NOT entity:PatientRecordSubject
          AND (
              entity.fhir_reference IS NULL
              OR NOT any(rule IN $resource_rules
                  WHERE entity.fhir_reference STARTS WITH rule.prefix
                    AND rule.label IN labels(entity))
          )
        WITH collect(entity) AS invalid
        FOREACH (entity IN invalid | DETACH DELETE entity)
        RETURN size(invalid) AS removed
        """,
        group_id=group_id,
        resource_rules=resource_rules,
    )
    invalid_entities_removed = int(records[0]["removed"] or 0) if records else 0

    records, _, _ = await client.driver.execute_query(
        """
        MATCH (source:Entity)-[edge:RELATES_TO]->(target:Entity)
        WHERE edge.group_id = $group_id
          AND NOT any(rule IN $edge_rules
              WHERE edge.name = rule.name
                AND rule.source IN labels(source)
                AND rule.target IN labels(target))
        WITH collect(edge) AS invalid
        FOREACH (edge IN invalid | DELETE edge)
        RETURN size(invalid) AS removed
        """,
        group_id=group_id,
        edge_rules=edge_rules,
    )
    invalid_edges_removed = int(records[0]["removed"] or 0) if records else 0

    records, _, _ = await client.driver.execute_query(
        """
        MATCH (:PatientRecordSubject)-[edge:RELATES_TO]->(:ClinicalObservation)
        WHERE edge.group_id = $group_id
          AND edge.name = 'HAS_CLINICAL_RESULT'
          AND any(marker IN $unsafe_markers
              WHERE toLower(coalesce(edge.fact, '')) CONTAINS marker)
        WITH collect(edge) AS unsafe
        FOREACH (edge IN unsafe | DELETE edge)
        RETURN size(unsafe) AS removed
        """,
        group_id=group_id,
        unsafe_markers=[
            "changed from",
            "decreased from",
            "increased from",
            "compared to",
            "compared with",
        ],
    )
    unsafe_result_edges_removed = int(records[0]["removed"] or 0) if records else 0
    return {
        "invalid_entities_removed": invalid_entities_removed,
        "invalid_edges_removed": invalid_edges_removed,
        "unsafe_result_edges_removed": unsafe_result_edges_removed,
    }


async def _ensure_episode_map_constraint(client: Any) -> None:
    await client.driver.execute_query(
        """
        CREATE CONSTRAINT avinia_memory_episode_map_key IF NOT EXISTS
        FOR (mapping:AviniaMemoryEpisodeMap)
        REQUIRE (mapping.group_id, mapping.logical_id, mapping.content_hash) IS UNIQUE
        """
    )


async def _reserve_episode_map(
    client: Any,
    episode: ClinicalMemoryEpisode,
    owner_token: str,
) -> dict[str, Any]:
    records, _, _ = await client.driver.execute_query(
        """
        MERGE (mapping:AviniaMemoryEpisodeMap {
            group_id: $group_id,
            logical_id: $logical_id,
            content_hash: $content_hash
        })
        ON CREATE SET mapping.owner_token = $owner_token,
                      mapping.status = 'pending',
                      mapping.created_at = datetime(),
                      mapping.lease_expires_at = datetime()
                          + duration({seconds: $lease_seconds})
        WITH mapping,
             mapping.status = 'failed'
             OR (mapping.status = 'pending'
                 AND (mapping.lease_expires_at IS NULL
                      OR mapping.lease_expires_at < datetime())) AS reclaim
        SET mapping.owner_token = CASE WHEN reclaim
                THEN $owner_token ELSE mapping.owner_token END,
            mapping.status = CASE WHEN reclaim
                THEN 'pending' ELSE mapping.status END,
            mapping.lease_expires_at = CASE WHEN reclaim
                THEN datetime() + duration({seconds: $lease_seconds})
                ELSE mapping.lease_expires_at END,
            mapping.updated_at = datetime()
        RETURN mapping.owner_token AS owner_token,
               mapping.status AS status,
               mapping.graphiti_uuid AS graphiti_uuid
        """,
        group_id=episode.group_id,
        logical_id=episode.logical_id,
        content_hash=episode.content_hash,
        owner_token=owner_token,
        lease_seconds=EPISODE_MAP_LEASE_SECONDS,
    )
    if not records:
        raise RuntimeError("Could not reserve the memory episode mapping")
    return dict(records[0])


async def _find_graphiti_episode_uuid(
    client: Any,
    episode: ClinicalMemoryEpisode,
) -> str | None:
    records, _, _ = await client.driver.execute_query(
        """
        MATCH (graphiti_episode:Episodic {name: $name, group_id: $group_id})
        RETURN graphiti_episode.uuid AS uuid
        ORDER BY graphiti_episode.created_at ASC
        LIMIT 1
        """,
        name=episode.name,
        group_id=episode.group_id,
        routing_="r",
    )
    return str(records[0]["uuid"]) if records else None


async def _complete_episode_map(
    client: Any,
    episode: ClinicalMemoryEpisode,
    *,
    owner_token: str,
    graphiti_uuid: str,
) -> str:
    records, _, _ = await client.driver.execute_query(
        """
        MATCH (mapping:AviniaMemoryEpisodeMap {
            group_id: $group_id,
            logical_id: $logical_id,
            content_hash: $content_hash,
            owner_token: $owner_token
        })
        SET mapping.graphiti_uuid = $graphiti_uuid,
            mapping.status = 'complete',
            mapping.completed_at = datetime()
        RETURN mapping.graphiti_uuid AS graphiti_uuid
        """,
        group_id=episode.group_id,
        logical_id=episode.logical_id,
        content_hash=episode.content_hash,
        owner_token=owner_token,
        graphiti_uuid=graphiti_uuid,
    )
    if not records:
        raise RuntimeError("The memory episode reservation was lost before completion")
    return str(records[0]["graphiti_uuid"])


async def _mark_episode_map_failed(
    client: Any,
    episode: ClinicalMemoryEpisode,
    owner_token: str,
) -> None:
    await client.driver.execute_query(
        """
        MATCH (mapping:AviniaMemoryEpisodeMap {
            group_id: $group_id,
            logical_id: $logical_id,
            content_hash: $content_hash,
            owner_token: $owner_token
        })
        WHERE mapping.status = 'pending'
        SET mapping.status = 'failed', mapping.failed_at = datetime()
        """,
        group_id=episode.group_id,
        logical_id=episode.logical_id,
        content_hash=episode.content_hash,
        owner_token=owner_token,
    )


def _episode_link(episode: ClinicalMemoryEpisode, graphiti_uuid: str) -> MemoryEpisodeLink:
    return MemoryEpisodeLink(
        logical_id=episode.logical_id,
        content_hash=episode.content_hash,
        graphiti_uuid=graphiti_uuid,
        fhir_references=episode.fhir_references,
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
