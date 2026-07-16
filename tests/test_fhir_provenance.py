"""Tests for the minimal Graphiti-to-FHIR provenance bridge."""

import unittest

from clinical_core.fhir_provenance import FHIRProvenanceBridge, ProvenanceConfig


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def consume(self):
        return None

    def single(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class _Session:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        if "RETURN count(DISTINCT memory) AS linked" in query:
            return _Result([{"linked": 1}])
        if "RETURN memory.uuid AS episode_uuid" in query:
            return _Result(
                [
                    {
                        "episode_uuid": "episode-1",
                        "source": {
                            "fhir_key": "Observation/a1c",
                            "resource_type": "Observation",
                            "resource_id": "a1c",
                            "fhir_version": "2",
                            "source_id": "store",
                        },
                    }
                ]
            )
        if "RETURN count(CASE WHEN memory.avinia_active" in query:
            return _Result([{"active": 1}])
        if "AS projected" in query:
            return _Result([{"projected": 1}])
        return _Result()


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self, **_kwargs):
        return self._session


class FHIRProvenanceBridgeTests(unittest.TestCase):
    def setUp(self):
        config = ProvenanceConfig(
            uri="neo4j+s://example",
            username="user",
            password="secret",
            database="neo4j",
            source_id="store",
        )
        self.session = _Session()
        self.bridge = FHIRProvenanceBridge(config)
        self.bridge._driver = _Driver(self.session)

    def test_episode_links_use_only_fhir_source_provenance_edges(self):
        linked = self.bridge.link_episodes(
            "john",
            group_id="patient-group",
            episodes=[
                {
                    "uuid": "episode-1",
                    "saga": "patient_clinical_journey",
                    "fhir_keys": ["Observation/a1c"],
                    "sources": [
                        {
                            "graph_key": "store|Observation/a1c",
                            "fhir_key": "Observation/a1c",
                        }
                    ],
                }
            ],
        )

        self.assertEqual(linked, 1)
        query = next(
            query
            for query, _ in self.session.calls
            if "RETURN count(DISTINCT memory) AS linked" in query
        )
        self.assertIn("[derived:DERIVED_FROM]", query)
        self.assertNotIn("SUPPORTED_BY", query)
        self.assertNotIn("ClinicalEvent", query)
        self.assertNotIn("ClinicalConcept", query)

    def test_source_lookup_is_patient_and_group_scoped(self):
        result = self.bridge.sources_for_episodes(
            "john",
            group_id="patient-group",
            episode_uuids=["episode-1"],
        )

        self.assertEqual(result["episode-1"][0]["fhir_key"], "Observation/a1c")
        query, parameters = next(
            call
            for call in self.session.calls
            if "RETURN memory.uuid AS episode_uuid" in call[0]
        )
        self.assertIn("derived.patient_id = $patient_id", query)
        self.assertIn("derived.group_id = $group_id", query)
        self.assertEqual(parameters["patient_id"], "john")
        self.assertEqual(parameters["group_id"], "patient-group")

    def test_patient_view_reuses_graphiti_nodes_with_readable_edges(self):
        result = self.bridge.project_patient_view(
            group_id="patient-group",
            episode_uuids=["episode-1"],
        )

        self.assertTrue(result["projected"])
        self.assertEqual(result["patients"], 1)
        self.assertEqual(result["episodes"], 1)
        queries = "\n".join(query for query, _ in self.session.calls)
        self.assertIn("SET entity:Patient", queries)
        self.assertIn("SET episode:PatientEpisode", queries)
        self.assertIn("[edge:HAS_EPISODE]", queries)
        self.assertIn("[edge:HAS_VISIT]", queries)
        self.assertIn("[edge:RECORDS]", queries)
        self.assertIn("[edge:HAS_CONDITION]", queries)
        self.assertIn("[edge:HAS_MEDICATION]", queries)
        self.assertIn("[edge:HAS_RESULT]", queries)
        self.assertIn("[edge:REPORTED_SYMPTOM]", queries)
        self.assertIn("[edge:RECORDED_DURING]", queries)
        self.assertIn("edge.avinia_projection = $projection", queries)
        self.assertNotIn("CREATE (", queries)

    def test_empty_patient_view_removes_stale_projection(self):
        result = self.bridge.project_patient_view(
            group_id="patient-group",
            episode_uuids=[],
        )

        self.assertTrue(result["projected"])
        self.assertEqual(result["total_edges"], 0)
        queries = "\n".join(query for query, _ in self.session.calls)
        self.assertIn("DELETE edge", queries)
        self.assertIn("REMOVE episode:PatientEpisode", queries)
        self.assertIn("REMOVE entity:Condition", queries)


if __name__ == "__main__":
    unittest.main()
