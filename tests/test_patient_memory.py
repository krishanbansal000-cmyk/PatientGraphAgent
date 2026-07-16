"""Tests for explicit patient memory sync and provenance-gated retrieval."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.patient_memory import PatientMemoryService
from clinical_core.patient_journey import patient_group_id


class _ImmediateMemoryService(PatientMemoryService):
    def _sync_worker(self, patient_id, resources, source, fingerprint):
        self._set_status(
            patient_id,
            state="succeeded",
            fingerprint=fingerprint,
            completed_at="now",
        )


class _FakeBridge:
    def __init__(self):
        self.query_patient = None

    def sources_for_episodes(self, patient_id, *, group_id, episode_uuids, limit):
        self.query_patient = patient_id
        self.query_group = group_id
        return {
            "episode-grounded": [
                {
                    "fhir_key": "Observation/a1c",
                    "resource_type": "Observation",
                    "resource_id": "a1c",
                }
            ]
        }


class _FakeGraphitiClient:
    def __init__(self):
        self.closed = False

    async def search(self, question, *, group_ids, num_results):
        self.group_ids = group_ids
        return [
            SimpleNamespace(
                fact="HbA1c increased",
                name="CHANGED",
                episodes=["episode-grounded"],
                valid_at=None,
                invalid_at=None,
            ),
            SimpleNamespace(
                fact="Unsupported generated fact",
                name="RELATES_TO",
                episodes=["episode-without-provenance"],
                valid_at=None,
                invalid_at=None,
            ),
        ]

    async def close(self):
        self.closed = True


class _FakeSyncBridge:
    def __init__(self):
        self.config = SimpleNamespace(source_id="test-fhir-store")
        self.active_episode_uuids = None
        self.projected_episode_uuids = None

    def set_active_episodes(self, patient_id, *, group_id, episode_uuids):
        self.active_episode_uuids = list(episode_uuids)
        return len(episode_uuids)

    def project_patient_view(self, *, group_id, episode_uuids):
        self.projected_episode_uuids = list(episode_uuids)
        return {
            "projected": True,
            "patients": 0,
            "episodes": len(episode_uuids),
            "total_edges": 0,
        }


class PatientMemoryTests(unittest.TestCase):
    def test_sync_now_waits_and_marks_synchronous_execution(self):
        service = _ImmediateMemoryService()

        status = service.sync_now(
            "john",
            [{"resourceType": "Patient", "id": "john"}],
            source="test",
        )

        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["execution"], "synchronous")

    def test_graphiti_search_is_patient_partitioned_and_provenance_gated(self):
        service = PatientMemoryService()
        bridge = _FakeBridge()
        client = _FakeGraphitiClient()

        with patch.dict(
            "sys.modules",
            {
                "clinical_core.graphiti_client": SimpleNamespace(
                    graphiti_is_configured=lambda: True,
                    create_graphiti_client=lambda: client,
                )
            },
        ):
            facts, excluded = asyncio.run(
                service._search_graphiti("john", "How is my A1c?", bridge, 8)
            )

        self.assertEqual(client.group_ids, [patient_group_id("john")])
        self.assertTrue(client.closed)
        self.assertEqual(bridge.query_patient, "john")
        self.assertEqual(bridge.query_group, patient_group_id("john"))
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["sources"][0]["fhir_key"], "Observation/a1c")
        self.assertEqual(excluded, 1)

    def test_retrieve_can_bridge_graphiti_search_from_running_event_loop(self):
        service = PatientMemoryService()
        bridge = _FakeBridge()
        client = _FakeGraphitiClient()

        async def retrieve_inside_loop():
            with patch("clinical_core.patient_memory.get_fhir_provenance_bridge", return_value=bridge), patch.dict(
                "sys.modules",
                {
                    "clinical_core.graphiti_client": SimpleNamespace(
                        graphiti_is_configured=lambda: True,
                        create_graphiti_client=lambda: client,
                    )
                },
            ):
                return service.retrieve("john", "How is my A1c?")

        result = asyncio.run(retrieve_inside_loop())

        self.assertEqual(len(result["facts"]), 1)
        self.assertNotIn("graphiti_fallback", result)

    def test_provenance_rows_preserve_fhir_version(self):
        service = PatientMemoryService()
        episode_link = SimpleNamespace(
            graphiti_uuid="episode-1",
            fhir_references=["Condition/c1"],
        )
        resources = [
            {
                "resourceType": "Condition",
                "id": "c1",
                "meta": {"versionId": "7", "lastUpdated": "2026-07-01T00:00:00Z"},
            }
        ]

        rows = service._build_provenance_rows(
            resources, [episode_link], source_id="fhir-store"
        )

        source = rows[0]["sources"][0]
        self.assertEqual(source["graph_key"], "fhir-store|Condition/c1")
        self.assertNotIn("patient_id", source)
        self.assertEqual(source["fhir_version"], "7")

    def test_successful_empty_sync_clears_patient_view(self):
        service = PatientMemoryService()
        bridge = _FakeSyncBridge()
        client = _FakeGraphitiClient()
        ingestion_result = SimpleNamespace(
            configured=True,
            attempted=0,
            ingested=0,
            reused=0,
            skipped=0,
            invalid_entities_removed=0,
            invalid_edges_removed=0,
            unsafe_result_edges_removed=0,
            episode_links=[],
            error=None,
        )

        async def ingest_memory_episodes(client, episodes):
            return ingestion_result

        with patch.dict(
            "sys.modules",
            {
                "clinical_core.graphiti_client": SimpleNamespace(
                    graphiti_is_configured=lambda: True,
                    create_graphiti_client=lambda: client,
                ),
                "clinical_core.graphiti_ingestion": SimpleNamespace(
                    PATIENT_JOURNEY_SAGA="patient_clinical_journey",
                    build_memory_episodes=lambda journey: [],
                    ingest_memory_episodes=ingest_memory_episodes,
                ),
            },
        ):
            result = asyncio.run(
                service._sync_graphiti(
                    "john",
                    [{"resourceType": "Patient", "id": "john"}],
                    "test",
                    bridge,
                )
            )

        self.assertTrue(result["synced"])
        self.assertEqual(bridge.active_episode_uuids, [])
        self.assertEqual(bridge.projected_episode_uuids, [])
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
