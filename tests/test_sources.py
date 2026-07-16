import unittest

from assistant.sources import dailymed_source, fhir_source, pubmed_source


class SourceMetadataTests(unittest.TestCase):
    def test_fhir_source_contains_drawer_target(self):
        source = fhir_source(
            {
                "resourceType": "Observation",
                "id": "a1c-1",
                "status": "final",
                "effectiveDateTime": "2026-07-01T10:00:00Z",
                "code": {"text": "Hemoglobin A1c"},
            }
        )

        self.assertEqual(source["title"], "Hemoglobin A1c")
        self.assertEqual(source["resource_type"], "Observation")
        self.assertEqual(source["resource_id"], "a1c-1")

    def test_dailymed_source_links_to_set_id(self):
        source = dailymed_source({"set_id": "abc-123", "title": "Hydrochlorothiazide label"})

        self.assertEqual(source["type"], "drug_label")
        self.assertIn("setid=abc-123", source["url"])

    def test_pubmed_source_links_to_article(self):
        source = pubmed_source(
            {
                "pmid": "12345678",
                "title": "Clinical evidence",
                "journal": "Medical Journal",
                "published_at": "2025-01-07",
                "doi": "10.1000/example",
            }
        )

        self.assertEqual(source["type"], "medical_literature")
        self.assertEqual(source["pmid"], "12345678")
        self.assertEqual(source["journal"], "Medical Journal")
        self.assertEqual(source["url"], "https://pubmed.ncbi.nlm.nih.gov/12345678/")


if __name__ == "__main__":
    unittest.main()
