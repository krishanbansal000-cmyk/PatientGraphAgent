"""Tests for bounded, deterministic terminology enrichment."""

import unittest

from clinical_core.terminology_enrichment import (
    TerminologyConfig,
    TerminologyEnricher,
    collect_fhir_codings,
    terminology_sources,
)


def coded_resource(resource_type, resource_id, system, code, display):
    return {
        "resourceType": resource_type,
        "id": resource_id,
        "code": {
            "coding": [
                {"system": system, "code": code, "display": display}
            ]
        },
    }


class StubEnricher(TerminologyEnricher):
    def _lookup_rxnorm(self, codes):
        return {
            "860975": {
                "canonical_display": "metformin hydrochloride 500 MG Oral Tablet",
                "version": "2026-07-06",
                "dataset": self.config.rxnorm_table,
                "match_method": "exact RxCUI",
            }
        }, {
            "id": self.config.rxnorm_table,
            "vocabulary": "RxNorm",
            "version": "2026-07-06",
            "publisher": "NLM",
            "url": "https://example.test/rxnorm",
        }

    def _lookup_icd10(self, codes):
        return {
            "E119": {
                "canonical_display": "Type 2 diabetes mellitus without complications",
                "version": "2026",
                "dataset": self.config.icd10_table,
                "match_method": "exact ICD-10-CM code",
            }
        }, {
            "id": self.config.icd10_table,
            "vocabulary": "ICD-10-CM",
            "version": "2026",
            "publisher": "CDC",
            "url": "https://example.test/icd10cm",
        }


class TerminologyEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.config = TerminologyConfig(
            enabled=True,
            location="US",
            rxnorm_table="bigquery-public-data.nlm_rxnorm.rxnconso_07_26",
            rxnorm_version="2026-07-06",
            icd10_table="avinia-app.medical_terminology.icd10cm_2026",
            icd10_version="2026-04-01",
        )

    def test_collects_only_supported_clinical_codings(self):
        resources = [
            coded_resource(
                "Condition", "c1", "http://snomed.info/sct", "44054006", "Diabetes"
            ),
            coded_resource(
                "Observation", "o1", "http://loinc.org", "4548-4", "HbA1c"
            ),
        ]
        concepts = collect_fhir_codings(resources)
        self.assertEqual({row["vocabulary"] for row in concepts}, {"SNOMED CT", "LOINC"})
        self.assertTrue(all(row["validation_status"] == "source_only" for row in concepts))

    def test_exact_bigquery_matches_are_versioned_and_citable(self):
        medication = {
            "resourceType": "MedicationRequest",
            "id": "m1",
            "medicationCodeableConcept": {
                "coding": [
                    {
                        "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                        "code": "860975",
                        "display": "Metformin 500 MG",
                    }
                ]
            },
        }
        diagnosis = coded_resource(
            "Condition",
            "c1",
            "http://hl7.org/fhir/sid/icd-10-cm",
            "E11.9",
            "Type 2 diabetes",
        )
        context = StubEnricher(config=self.config).enrich([medication, diagnosis])
        by_vocabulary = {row["vocabulary"]: row for row in context["concepts"]}

        self.assertEqual(by_vocabulary["RxNorm"]["validation_status"], "validated")
        self.assertEqual(by_vocabulary["ICD-10-CM"]["version"], "2026")
        self.assertEqual(len(terminology_sources(context)), 2)

    def test_does_not_infer_cross_vocabulary_mappings(self):
        condition = coded_resource(
            "Condition", "c1", "http://snomed.info/sct", "44054006", "Diabetes"
        )
        context = StubEnricher(config=self.config).enrich([condition])
        concept = context["concepts"][0]
        self.assertEqual(concept["validation_status"], "source_only")
        self.assertNotIn("mapped_code", concept)
        self.assertEqual(context["datasets"], [])


if __name__ == "__main__":
    unittest.main()
