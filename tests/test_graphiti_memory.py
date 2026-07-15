"""Unit tests for the optional MedGraphiti patient-memory layer."""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agent.clinical_graph_schema import EDGE_TYPE_MAP, EDGE_TYPES, ENTITY_TYPES
from agent.graphiti_client import GraphitiSettings, create_graphiti_client
from agent.graphiti_ingestion import build_memory_episodes, ingest_memory_episodes
from agent.patient_journey import build_patient_journey


def _patient() -> dict:
    return {"resourceType": "Patient", "id": "john", "birthDate": "1970-01-01"}


def _observation(resource_id: str, date: str | None, value: float) -> dict:
    resource = {
        "resourceType": "Observation",
        "id": resource_id,
        "status": "final",
        "code": {
            "text": "HbA1c",
            "coding": [{"system": "http://loinc.org", "code": "4548-4"}],
        },
        "valueQuantity": {"value": value, "unit": "%"},
        "meta": {"versionId": "2"},
    }
    if date:
        resource["effectiveDateTime"] = date
    return resource


class ClinicalGraphSchemaTests(unittest.TestCase):
    def test_schema_uses_specific_clinical_names_and_valid_edge_signatures(self):
        self.assertIn("ClinicalCondition", ENTITY_TYPES)
        self.assertIn("MedicationTherapy", ENTITY_TYPES)
        self.assertIn("ClinicalObservation", ENTITY_TYPES)
        self.assertIn("ClinicalAllergy", ENTITY_TYPES)
        self.assertIn("ClinicalImmunization", ENTITY_TYPES)
        self.assertIn("HAS_ENCOUNTER", EDGE_TYPES)
        self.assertNotIn("MEDICATION_INTERACTS_WITH_MEDICATION", EDGE_TYPES)
        self.assertEqual(
            EDGE_TYPE_MAP[("PatientRecordSubject", "ClinicalCondition")],
            ["HAS_CONDITION"],
        )
        self.assertEqual(
            EDGE_TYPE_MAP[("PatientRecordSubject", "ClinicalEncounter")],
            ["HAS_ENCOUNTER"],
        )

        from graphiti_core.utils.ontology_utils.entity_types_utils import (
            validate_entity_types,
        )

        validate_entity_types(ENTITY_TYPES)


class GraphitiClientTests(unittest.TestCase):
    def test_client_is_disabled_without_explicit_feature_flag(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = GraphitiSettings.from_environment()
            self.assertFalse(settings.configured)
            self.assertIsNone(create_graphiti_client(settings))

    def test_invalid_concurrency_setting_falls_back_without_breaking_startup(self):
        with patch.dict(os.environ, {"MEDGRAPHITI_MAX_COROUTINES": "invalid"}):
            settings = GraphitiSettings.from_environment()

        self.assertEqual(settings.max_coroutines, 2)
        self.assertEqual(os.environ["GRAPHITI_TELEMETRY_ENABLED"], "false")

    def test_vertex_client_never_stores_raw_episode_content(self):
        settings = GraphitiSettings(
            enabled=True,
            neo4j_uri="neo4j+s://example.databases.neo4j.io",
            neo4j_username="user",
            neo4j_password="secret",
            neo4j_database="neo4j",
            google_cloud_project="avinia-app",
            google_cloud_location="us-central1",
            llm_model="gemini-2.5-flash",
            small_llm_model="gemini-2.5-flash-lite",
            embedding_model="text-embedding-005",
            embedding_dimension=768,
            reranker_model="gemini-2.5-flash-lite",
            max_coroutines=3,
        )
        sentinel = object()
        with (
            patch("google.genai.Client") as vertex_client,
            patch("graphiti_core.Graphiti", return_value=sentinel) as graphiti,
            patch("graphiti_core.driver.neo4j_driver.Neo4jDriver") as driver,
            patch("graphiti_core.llm_client.gemini_client.GeminiClient"),
            patch("graphiti_core.embedder.gemini.GeminiEmbedder") as embedder,
            patch("graphiti_core.cross_encoder.gemini_reranker_client.GeminiRerankerClient"),
        ):
            client = create_graphiti_client(settings)

        self.assertIs(client, sentinel)
        vertex_client.assert_called_once()
        vertex_options = vertex_client.call_args.kwargs
        self.assertEqual(vertex_options["project"], "avinia-app")
        self.assertEqual(vertex_options["location"], "us-central1")
        retry_options = vertex_options["http_options"].retry_options
        self.assertEqual(retry_options.attempts, 4)
        self.assertIn(429, retry_options.http_status_codes)
        driver.assert_called_once_with(
            settings.neo4j_uri,
            settings.neo4j_username,
            settings.neo4j_password,
            database="neo4j",
        )
        self.assertFalse(graphiti.call_args.kwargs["store_raw_episode_content"])
        self.assertEqual(graphiti.call_args.kwargs["max_coroutines"], 3)
        self.assertEqual(embedder.call_args.kwargs["config"].embedding_dim, 768)

    def test_pinned_graphiti_keeps_patient_group_as_property_on_neo4j(self):
        from graphiti_core.driver.driver import GraphDriver
        from graphiti_core.driver.neo4j_driver import Neo4jDriver

        # Graphiti.add_episode calls clone(group_id). For Neo4j 0.29.2 this is
        # deliberately the base no-op, so Aura stays on its one configured DB.
        self.assertIs(Neo4jDriver.clone, GraphDriver.clone)


class MemoryEpisodeConversionTests(unittest.TestCase):
    def test_conversion_is_stable_ordered_and_source_linked(self):
        resources = [
            _patient(),
            _observation("a1c-july", "2026-07-01T09:00:00Z", 8.1),
            _observation("a1c-june", "2026-06-01T09:00:00Z", 7.4),
        ]
        journey = build_patient_journey("john", resources)

        first = build_memory_episodes(journey)
        second = build_memory_episodes(journey)

        self.assertEqual(
            [item.logical_id for item in first], [item.logical_id for item in second]
        )
        self.assertEqual([item.content_hash for item in first], [item.content_hash for item in second])
        self.assertEqual([item.reference_time.month for item in first], [6, 7])
        self.assertTrue(all(item.group_id == journey.group_id for item in first))
        self.assertNotIn("john", first[0].body)
        body = json.loads(first[0].body)
        self.assertEqual(body["clinical_items"][0]["terminology_code"], "4548-4")
        self.assertNotIn("value", body["clinical_items"][0])
        self.assertNotIn("changes", body)
        self.assertEqual(body["fhir_sources"][0]["reference"], "Observation/a1c-june")
        self.assertEqual(first[0].fhir_references, ["Observation/a1c-june"])

    def test_changed_fhir_content_creates_a_new_revision_of_same_logical_episode(self):
        before = build_patient_journey(
            "john", [_patient(), _observation("a1c", "2026-06-01", 7.4)]
        )
        after = build_patient_journey(
            "john", [_patient(), _observation("a1c", "2026-06-01", 8.1)]
        )

        first = build_memory_episodes(before)[0]
        revision = build_memory_episodes(after)[0]

        self.assertEqual(first.logical_id, revision.logical_id)
        self.assertNotEqual(first.content_hash, revision.content_hash)
        self.assertNotEqual(first.name, revision.name)

    def test_undated_episode_is_omitted_from_temporal_memory_by_default(self):
        journey = build_patient_journey("john", [_patient(), _observation("undated", None, 7.0)])

        self.assertEqual(build_memory_episodes(journey), [])
        included = build_memory_episodes(journey, include_undated=True)
        self.assertEqual(len(included), 1)
        self.assertTrue(json.loads(included[0].body)["episode"]["clinical_date_unknown"])

    def test_dense_visit_is_split_into_bounded_source_linked_memory_parts(self):
        encounter = {
            "resourceType": "Encounter",
            "id": "dense-lab-visit",
            "status": "finished",
            "class": {"display": "Ambulatory"},
            "period": {"start": "2026-06-01T09:00:00Z"},
        }
        observations = []
        for index in range(13):
            item = _observation(f"lab-{index}", "2026-06-01T09:15:00Z", 7.0 + index)
            item["encounter"] = {"reference": "Encounter/dense-lab-visit"}
            observations.append(item)
        journey = build_patient_journey("john", [_patient(), encounter, *observations])

        memory_episodes = build_memory_episodes(journey)

        self.assertEqual(len(memory_episodes), 3)
        bodies = [json.loads(item.body) for item in memory_episodes]
        self.assertTrue(all(len(body["clinical_items"]) <= 6 for body in bodies))
        self.assertTrue(
            all(
                "Encounter/dense-lab-visit" in item.fhir_references
                for item in memory_episodes
            )
        )
        observation_references = {
            reference
            for item in memory_episodes
            for reference in item.fhir_references
            if reference.startswith("Observation/")
        }
        self.assertEqual(len(observation_references), 13)


class MemoryIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingestion_is_sequential_patient_scoped_and_saga_linked(self):
        journey = build_patient_journey(
            "john",
            [
                _patient(),
                _observation("a1c-july", "2026-07-01", 8.1),
                _observation("a1c-june", "2026-06-01", 7.4),
            ],
        )
        episodes = build_memory_episodes(journey)
        client = MagicMock()
        client.build_indices_and_constraints = AsyncMock()
        client.driver.execute_query = AsyncMock()

        async def execute_query(query, **kwargs):
            if "CREATE CONSTRAINT" in query:
                return [], None, None
            if "MERGE (mapping:AviniaMemoryEpisodeMap" in query:
                return [
                    {
                        "owner_token": kwargs["owner_token"],
                        "status": "pending",
                        "graphiti_uuid": None,
                    }
                ], None, None
            if "MATCH (graphiti_episode:Episodic" in query:
                return [], None, None
            if "SET mapping.graphiti_uuid" in query:
                return [{"graphiti_uuid": kwargs["graphiti_uuid"]}], None, None
            if "RETURN size(invalid) AS removed" in query:
                return [{"removed": 0}], None, None
            if "RETURN size(unsafe) AS removed" in query:
                return [{"removed": 0}], None, None
            raise AssertionError(f"Unexpected query: {query}")

        client.driver.execute_query.side_effect = execute_query

        created_count = 0

        async def add_episode(**kwargs):
            nonlocal created_count
            created_count += 1
            return SimpleNamespace(episode=SimpleNamespace(uuid=f"generated-{created_count}"))

        client.add_episode = AsyncMock(side_effect=add_episode)

        result = await ingest_memory_episodes(client, list(reversed(episodes)))

        self.assertTrue(result.configured)
        self.assertEqual(result.ingested, 2, result.error)
        self.assertEqual(result.episode_uuids, ["generated-1", "generated-2"])
        self.assertEqual(
            [link.logical_id for link in result.episode_links],
            [episode.logical_id for episode in episodes],
        )
        client.build_indices_and_constraints.assert_awaited_once_with()
        calls = client.add_episode.await_args_list
        self.assertEqual(calls[0].kwargs["group_id"], journey.group_id)
        self.assertEqual(calls[1].kwargs["group_id"], journey.group_id)
        self.assertNotIn("uuid", calls[0].kwargs)
        self.assertNotIn("uuid", calls[1].kwargs)
        self.assertEqual(calls[0].kwargs["saga"], "patient_clinical_journey")
        self.assertIsNone(calls[0].kwargs["saga_previous_episode_uuid"])
        self.assertEqual(
            calls[1].kwargs["saga_previous_episode_uuid"], "generated-1"
        )
        sanitizer_queries = [
            call.args[0]
            for call in client.driver.execute_query.await_args_list
            if call.args and "MATCH (entity:Entity" in call.args[0]
        ]
        self.assertEqual(len(sanitizer_queries), 1)
        self.assertTrue(
            all("entity.fhir_reference IS NULL" in query for query in sanitizer_queries)
        )

    async def test_existing_deterministic_episode_is_reused_without_llm_ingestion(self):
        journey = build_patient_journey(
            "john", [_patient(), _observation("a1c", "2026-06-01", 7.4)]
        )
        episode = build_memory_episodes(journey)[0]
        client = MagicMock()
        client.build_indices_and_constraints = AsyncMock()
        client.add_episode = AsyncMock()
        async def execute_query(query, **kwargs):
            if "CREATE CONSTRAINT" in query:
                return [], None, None
            if "MERGE (mapping:AviniaMemoryEpisodeMap" in query:
                return [
                    {
                        "owner_token": "original-worker",
                        "status": "complete",
                        "graphiti_uuid": "graphiti-existing",
                    }
                ], None, None
            if "RETURN size(invalid) AS removed" in query:
                return [{"removed": 0}], None, None
            if "RETURN size(unsafe) AS removed" in query:
                return [{"removed": 0}], None, None
            raise AssertionError(f"Unexpected query: {query}")

        client.driver.execute_query = AsyncMock(side_effect=execute_query)

        result = await ingest_memory_episodes(client, [episode])

        self.assertEqual(result.ingested, 1)
        self.assertEqual(result.reused, 1)
        self.assertEqual(result.episode_uuids, ["graphiti-existing"])
        self.assertEqual(result.episode_links[0].logical_id, episode.logical_id)
        client.add_episode.assert_not_awaited()

    async def test_none_client_is_a_cleanly_disabled_noop(self):
        result = await ingest_memory_episodes(None, [])

        self.assertFalse(result.configured)
        self.assertEqual(result.ingested, 0)


if __name__ == "__main__":
    unittest.main()
