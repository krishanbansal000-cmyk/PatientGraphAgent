"""Patient-scoped Graphiti memory backed by canonical FHIR provenance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from agent.fhir_provenance import FHIRProvenanceBridge, get_fhir_provenance_bridge
from agent.patient_journey import build_patient_journey, patient_group_id


log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resource_fingerprint(resources: Sequence[Dict[str, Any]]) -> str:
    versions = []
    for resource in resources:
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        if not resource_type or not resource_id:
            continue
        meta = resource.get("meta") if isinstance(resource.get("meta"), dict) else {}
        versions.append(
            (
                f"{resource_type}/{resource_id}",
                str(meta.get("versionId") or ""),
                str(meta.get("lastUpdated") or ""),
            )
        )
    return hashlib.sha256(
        json.dumps(sorted(versions), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_status(status: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy suitable for API/tool responses."""
    return {
        key: value
        for key, value in status.items()
        if key not in {"fingerprint"}
    }


class PatientMemoryService:
    """Explicit memory synchronization and bounded semantic retrieval."""

    def __init__(self):
        self._async_bridge = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="patient-memory-async",
        )
        self._lock = threading.Lock()
        self._status: Dict[str, Dict[str, Any]] = {}

    def status(self, patient_id: str) -> Dict[str, Any]:
        with self._lock:
            status = self._status.get(patient_id)
            if status is None:
                return {
                    "patient_id": patient_id,
                    "group_id": patient_group_id(patient_id),
                    "state": "not_rebuilt_in_this_process",
                    "execution": "explicit_synchronous",
                }
            return _safe_status(dict(status))

    def sync_now(
        self,
        patient_id: str,
        resources: Sequence[Dict[str, Any]],
        *,
        source: str,
    ) -> Dict[str, Any]:
        """Rebuild memory and provenance before returning."""
        fingerprint = _resource_fingerprint(resources)
        with self._lock:
            self._status[patient_id] = {
                "patient_id": patient_id,
                "group_id": patient_group_id(patient_id),
                "state": "queued",
                "execution": "synchronous",
                "queued_at": _utc_now(),
                "fingerprint": fingerprint,
                "provenance_bridge": {"configured": False, "linked": False},
                "graphiti": {"configured": False, "synced": False},
            }
        self._sync_worker(patient_id, list(resources), source, fingerprint)
        with self._lock:
            self._status[patient_id]["execution"] = "synchronous"
        return self.status(patient_id)

    def _set_status(self, patient_id: str, **updates: Any) -> None:
        with self._lock:
            if patient_id in self._status:
                self._status[patient_id].update(updates)

    def _sync_worker(
        self,
        patient_id: str,
        resources: Sequence[Dict[str, Any]],
        source: str,
        fingerprint: str,
    ) -> None:
        self._set_status(patient_id, state="running", started_at=_utc_now())
        bridge = get_fhir_provenance_bridge()
        provenance = {"configured": bridge is not None, "linked": False}
        graphiti = {"configured": False, "synced": False}

        try:
            graphiti = asyncio.run(
                self._sync_graphiti(patient_id, resources, source, bridge)
            )
            provenance.update(graphiti.pop("provenance_bridge", {}))
        except Exception as exc:
            graphiti = {
                "configured": True,
                "synced": False,
                "fallback": f"unavailable ({type(exc).__name__})",
            }
            log.warning("Graphiti patient memory sync failed: %s", type(exc).__name__)

        graphiti_ready = bool(graphiti.get("configured") and graphiti.get("synced"))
        provenance_ready = bool(
            provenance.get("configured") and provenance.get("linked")
        )
        state = "succeeded" if graphiti_ready and provenance_ready else "failed"
        if not graphiti.get("configured"):
            state = "disabled"
        self._set_status(
            patient_id,
            state=state,
            completed_at=_utc_now(),
            fingerprint=fingerprint,
            provenance_bridge=provenance,
            graphiti=graphiti,
        )

    async def _sync_graphiti(
        self,
        patient_id: str,
        resources: Sequence[Dict[str, Any]],
        source: str,
        bridge: Optional[FHIRProvenanceBridge],
    ) -> Dict[str, Any]:
        # Imports are intentionally lazy: the app still works without the
        # optional graphiti-core dependency or its model configuration.
        from agent.graphiti_client import create_graphiti_client, graphiti_is_configured
        from agent.graphiti_ingestion import (
            PATIENT_JOURNEY_SAGA,
            build_memory_episodes,
            ingest_memory_episodes,
        )

        configured = graphiti_is_configured()
        if not configured:
            return {"configured": False, "synced": False}
        if bridge is None:
            return {
                "configured": True,
                "synced": False,
                "fallback": "FHIR provenance bridge unavailable",
                "provenance_bridge": {"configured": False, "linked": False},
            }
        client = create_graphiti_client()
        if client is None:
            return {
                "configured": True,
                "synced": False,
                "fallback": "Graphiti client unavailable",
            }

        journey = build_patient_journey(patient_id, resources, source=source)
        episodes = build_memory_episodes(journey)
        try:
            result = await ingest_memory_episodes(client, episodes)
        finally:
            await client.close()

        response = {
            "configured": bool(result.configured),
            "synced": result.error is None,
            "attempted": int(result.attempted),
            "ingested": int(result.ingested),
            "reused": int(result.reused),
            "skipped": int(result.skipped),
            "invalid_entities_removed": int(result.invalid_entities_removed),
            "invalid_edges_removed": int(result.invalid_edges_removed),
            "unsafe_result_edges_removed": int(result.unsafe_result_edges_removed),
        }
        if result.error:
            error_type = str(result.error).split(":", 1)[0][:80]
            response["fallback"] = f"ingestion failed ({error_type})"

        if result.episode_links:
            provenance_rows = self._build_provenance_rows(
                resources,
                result.episode_links,
                source_id=bridge.config.source_id,
                saga=PATIENT_JOURNEY_SAGA,
            )
            linked = bridge.link_episodes(
                patient_id,
                group_id=journey.group_id,
                episodes=provenance_rows,
            )
            response["provenance_bridge"] = {
                "configured": True,
                "linked": linked == len(result.episode_links),
                "linked_episodes": linked,
            }
            if result.error is None:
                response["active_episodes"] = bridge.set_active_episodes(
                    patient_id,
                    group_id=journey.group_id,
                    episode_uuids=[
                        link.graphiti_uuid for link in result.episode_links
                    ],
                )
        else:
            response["provenance_bridge"] = {
                "configured": True,
                "linked": result.error is None,
                "linked_episodes": 0,
            }
        return response

    @staticmethod
    def _build_provenance_rows(
        resources: Sequence[Dict[str, Any]],
        episode_links: Sequence[Any],
        *,
        source_id: str,
        saga: str = "patient_clinical_journey",
    ) -> List[Dict[str, Any]]:
        resource_by_key = {
            f"{resource.get('resourceType')}/{resource.get('id')}": resource
            for resource in resources
            if resource.get("resourceType") and resource.get("id")
        }
        rows: List[Dict[str, Any]] = []
        for link in episode_links[:500]:
            sources = []
            references = list(dict.fromkeys(link.fhir_references))[:50]
            for reference in references:
                resource = resource_by_key.get(reference)
                if resource is None:
                    continue
                resource_type, resource_id = reference.split("/", 1)
                meta = resource.get("meta") if isinstance(resource.get("meta"), dict) else {}
                sources.append(
                    {
                        "graph_key": f"{source_id}|{reference}",
                        "fhir_key": reference,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "source_id": source_id,
                        "fhir_version": meta.get("versionId"),
                        "last_updated": meta.get("lastUpdated"),
                    }
                )
            rows.append(
                {
                    "uuid": link.graphiti_uuid,
                    "saga": saga,
                    "fhir_keys": [source["fhir_key"] for source in sources],
                    "sources": sources,
                }
            )
        return rows

    def retrieve(
        self,
        patient_id: str,
        question: str,
        *,
        fact_limit: int = 8,
    ) -> Dict[str, Any]:
        """Return semantic Graphiti facts grounded in canonical FHIR sources."""
        from agent.graphiti_client import graphiti_is_configured

        bridge = get_fhir_provenance_bridge()
        result: Dict[str, Any] = {
            "configured": graphiti_is_configured() and bridge is not None,
            "used": False,
            "facts": [],
            "sync": self.status(patient_id),
        }

        def search_graphiti() -> tuple[List[Dict[str, Any]], int]:
            return asyncio.run(
                self._search_graphiti(
                    patient_id,
                    question[:2000],
                    bridge,
                    max(1, min(fact_limit, 12)),
                )
            )

        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                facts, excluded = search_graphiti()
            else:
                facts, excluded = self._async_bridge.submit(search_graphiti).result(
                    timeout=60
                )
            result["facts"] = facts
            result["ungrounded_facts_excluded"] = excluded
            result["used"] = result["used"] or bool(facts)
        except Exception as exc:
            result["graphiti_fallback"] = f"unavailable ({type(exc).__name__})"
        return result

    async def _search_graphiti(
        self,
        patient_id: str,
        question: str,
        bridge: Optional[FHIRProvenanceBridge],
        limit: int,
    ) -> tuple[List[Dict[str, Any]], int]:
        from agent.graphiti_client import create_graphiti_client, graphiti_is_configured

        if not graphiti_is_configured() or bridge is None:
            return [], 0
        client = create_graphiti_client()
        if client is None:
            return [], 0
        group_id = patient_group_id(patient_id)
        try:
            edges = await client.search(
                question,
                group_ids=[group_id],
                num_results=limit,
            )
        finally:
            await client.close()

        episode_uuids = list(
            dict.fromkeys(
                episode_uuid
                for edge in edges
                for episode_uuid in (getattr(edge, "episodes", None) or [])
            )
        )[:100]
        provenance = bridge.sources_for_episodes(
            patient_id,
            group_id=group_id,
            episode_uuids=episode_uuids,
            limit=100,
        )
        facts: List[Dict[str, Any]] = []
        excluded = 0
        for edge in edges[:limit]:
            edge_episode_ids = list(getattr(edge, "episodes", None) or [])[:20]
            sources = []
            for episode_uuid in edge_episode_ids:
                sources.extend(provenance.get(episode_uuid, []))
            deduplicated_sources = list(
                {source["fhir_key"]: source for source in sources}.values()
            )[:20]
            if not deduplicated_sources:
                excluded += 1
                continue
            facts.append(
                {
                    "fact": str(getattr(edge, "fact", ""))[:1200],
                    "relationship": str(getattr(edge, "name", ""))[:120],
                    "valid_at": _serialize_datetime(getattr(edge, "valid_at", None)),
                    "invalid_at": _serialize_datetime(getattr(edge, "invalid_at", None)),
                    "episode_uuids": edge_episode_ids,
                    "sources": deduplicated_sources,
                }
            )
        return facts, excluded


def _serialize_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)[:80]


_memory_service = PatientMemoryService()


def get_patient_memory_service() -> PatientMemoryService:
    return _memory_service
