import unittest
from types import SimpleNamespace

from api.citations import collect_event_citations


class FakeEvent:
    def __init__(self, responses):
        self._responses = responses

    def get_function_responses(self):
        return self._responses


class CitationTests(unittest.TestCase):
    def test_collects_deduplicates_and_numbers_tool_sources(self):
        source = {
            "id": "fhir:Condition/condition-1",
            "type": "patient_record",
            "title": "Hypertension",
            "publisher": "Connected FHIR record",
            "resource_type": "Condition",
            "resource_id": "condition-1",
        }
        events = [
            FakeEvent([SimpleNamespace(name="search_fhir", response={"sources": [source]})]),
            FakeEvent([SimpleNamespace(name="read_fhir", response={"result": {"sources": [source]}})]),
        ]

        citations = collect_event_citations(events)

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["number"], 1)
        self.assertEqual(citations[0]["tools"], ["search_fhir", "read_fhir"])

    def test_ignores_model_text_without_tool_response_sources(self):
        event = FakeEvent([SimpleNamespace(name="get_drug_info", response={"answer": "DailyMed says..."})])

        self.assertEqual(collect_event_citations([event]), [])

    def test_keeps_allowlisted_urls_and_strips_untrusted_urls(self):
        trusted = {
            "id": "dailymed:abc",
            "type": "drug_label",
            "title": "Official drug label",
            "url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=abc",
        }
        untrusted = {
            "id": "dailymed:def",
            "type": "drug_label",
            "title": "Untrusted label",
            "url": "https://example.com/not-a-source",
        }
        event = FakeEvent(
            [SimpleNamespace(name="get_drug_info", response={"sources": [trusted, untrusted]})]
        )

        citations = collect_event_citations([event])

        self.assertIn("url", citations[0])
        self.assertNotIn("url", citations[1])

    def test_keeps_versioned_terminology_provenance(self):
        source = {
            "id": "terminology:avinia-app.medical_terminology.icd10cm_2026:2026",
            "type": "terminology",
            "title": "ICD-10-CM terminology 2026",
            "publisher": "CDC National Center for Health Statistics",
            "dataset": "avinia-app.medical_terminology.icd10cm_2026",
            "version": "2026",
            "url": "https://ftp.cdc.gov/pub/Health_Statistics/",
        }
        event = FakeEvent(
            [SimpleNamespace(name="search_patient_context", response={"sources": [source]})]
        )

        citation = collect_event_citations([event])[0]
        self.assertEqual(citation["version"], "2026")
        self.assertEqual(citation["dataset"], source["dataset"])
        self.assertIn("url", citation)


if __name__ == "__main__":
    unittest.main()
