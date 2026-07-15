"""Tests for hybrid patient-context retrieval using synthetic FHIR records."""

import unittest

from agent.context_search import (
    PatientQueryPlan,
    build_patient_context,
    build_search_plan,
    resources_to_events,
)


PATIENT_ID = "john"


def planned_context(
    question,
    resources,
    *,
    intents,
    concepts=(),
    time_scope="all",
    date_start=None,
    date_end=None,
    output_mode="direct",
    scope="focused",
    references_prior_context=False,
    operation="search",
    target_code=None,
    prior_resource_ids=(),
    max_results=10,
):
    interpreted = PatientQueryPlan(
        intents=list(intents),
        concepts=list(concepts),
        time_scope=time_scope,
        date_start=date_start,
        date_end=date_end,
        output_mode=output_mode,
        scope=scope,
        references_prior_context=references_prior_context,
        operation=operation,
        target_code=target_code,
    )
    plan = build_search_plan(question, interpreted)
    return build_patient_context(
        question,
        resources,
        plan,
        prior_resource_ids=prior_resource_ids,
        max_results=max_results,
    )


def patient_resource():
    return {
        "resourceType": "Patient",
        "id": PATIENT_ID,
        "gender": "male",
        "birthDate": "1967-09-26",
    }


def encounter_resource():
    return {
        "resourceType": "Encounter",
        "id": "enc-diabetes",
        "status": "finished",
        "class": {"code": "AMB", "display": "Outpatient visit"},
        "subject": {"reference": f"Patient/{PATIENT_ID}"},
        "period": {"start": "2026-06-01T10:00:00Z"},
        "reasonCode": [{"text": "Diabetes follow-up"}],
    }


def condition_resource():
    return {
        "resourceType": "Condition",
        "id": "diabetes",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {
            "text": "Type 2 diabetes mellitus",
            "coding": [{"system": "http://snomed.info/sct", "code": "44054006"}],
        },
        "subject": {"reference": f"Patient/{PATIENT_ID}"},
        "encounter": {"reference": "Encounter/enc-diabetes"},
        "recordedDate": "2026-06-01",
    }


def medication_resource(name="Metformin 500 MG Oral Tablet", resource_id="metformin"):
    return {
        "resourceType": "MedicationRequest",
        "id": resource_id,
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {
            "text": name,
            "coding": [
                {
                    "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                    "code": "860975" if resource_id == "metformin" else "123",
                    "display": name,
                }
            ],
        },
        "subject": {"reference": f"Patient/{PATIENT_ID}"},
        "encounter": {"reference": "Encounter/enc-diabetes"},
        "authoredOn": "2026-06-01",
    }


def observation_resource(
    resource_id="hba1c",
    effective="2026-06-01T09:00:00Z",
    value=8.1,
    code="4548-4",
    name="Hemoglobin A1c",
):
    return {
        "resourceType": "Observation",
        "id": resource_id,
        "status": "final",
        "code": {
            "text": name,
            "coding": [{"system": "http://loinc.org", "code": code}],
        },
        "subject": {"reference": f"Patient/{PATIENT_ID}"},
        "encounter": {"reference": "Encounter/enc-diabetes"},
        "effectiveDateTime": effective,
        "valueQuantity": {"value": value, "unit": "%"},
    }


class PatientContextSearchTests(unittest.TestCase):
    def setUp(self):
        self.resources = [
            patient_resource(),
            encounter_resource(),
            condition_resource(),
            medication_resource(),
            observation_resource(),
        ]

    def test_model_plan_is_validated_and_materialized(self):
        interpreted = PatientQueryPlan(
            intents=["medication"],
            concepts=["metformin", "diabetes medicine"],
            time_scope="latest",
        )
        plan = build_search_plan(
            "Why was my sugar tablet changed last time?", interpreted
        )
        self.assertEqual(plan.intent, "medication")
        self.assertEqual(plan.time_scope, "latest")
        self.assertIn("metformin", plan.expanded_terms)
        self.assertIn("diabetes", plan.expanded_terms)
        self.assertEqual(
            plan.resource_types,
            ["MedicationRequest", "MedicationStatement", "MedicationDispense"],
        )

    def test_compound_question_searches_each_clinical_category(self):
        question = "Summarize my conditions and recent visits"
        interpreted = PatientQueryPlan(
            intents=["condition", "visit"],
            concepts=["conditions", "visits"],
            time_scope="latest",
            output_mode="summary",
            scope="broad",
        )
        plan = build_search_plan(question, interpreted)
        self.assertIn("condition", plan.intents)
        self.assertIn("visit", plan.intents)
        self.assertIn("Condition", plan.resource_types)
        self.assertIn("Encounter", plan.resource_types)

        result = build_patient_context(
            question, self.resources, plan
        )
        relevant_types = {
            item.event.resource_type for item in result.relevant_events
        }
        self.assertIn("Condition", relevant_types)
        self.assertIn("Encounter", relevant_types)

    def test_model_supplied_month_range_is_validated_and_applied(self):
        question = "Tell problems before July 2026, from Jan till June"
        interpreted = PatientQueryPlan(
            intents=["condition"],
            concepts=["problems", "conditions"],
            time_scope="historical",
            date_start="2026-01-01",
            date_end="2026-06-30",
        )
        plan = build_search_plan(question, interpreted)
        self.assertEqual(plan.date_start, "2026-01-01")
        self.assertEqual(plan.date_end, "2026-06-30")

        result = build_patient_context(question, self.resources, plan)
        self.assertGreater(result.date_filtered_resources, 0)
        self.assertTrue(
            all(
                "2026-01-01" <= item.event.event_time[:10] <= "2026-06-30"
                for item in result.relevant_events
                if item.event.event_time
            )
        )

    def test_broad_timeline_returns_chronological_events(self):
        result = planned_context(
            "Please query the data and tell the timeline",
            self.resources,
            intents=["general"],
            scope="broad",
            output_mode="timeline",
            operation="timeline",
        )
        self.assertEqual(result.plan.output_mode, "timeline")
        self.assertEqual(result.plan.scope, "broad")
        self.assertTrue(result.timeline_events)
        timestamps = [event.event_time for event in result.timeline_events]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_follow_up_can_reuse_previously_cited_resources(self):
        prior_ids = ["Condition/diabetes"]
        result = planned_context(
            "Check symptoms and possibilities due to this",
            self.resources,
            intents=["symptom", "condition"],
            concepts=["symptoms", "possible causes"],
            references_prior_context=True,
            prior_resource_ids=prior_ids,
        )
        self.assertTrue(result.plan.references_prior_context)
        self.assertTrue(result.conversation_context_used)
        self.assertEqual(result.relevant_events[0].event.key, prior_ids[0])

    def test_condition_onset_is_distinct_from_recorded_date(self):
        condition = condition_resource()
        condition["onsetDateTime"] = "2025-10-03"
        condition["recordedDate"] = "2026-06-01"
        event = resources_to_events([condition])[0]
        self.assertEqual(event.event_time, "2025-10-03")
        self.assertEqual(event.event_time_kind, "onset")
        self.assertEqual(event.recorded_time, "2026-06-01")

    def test_vague_medication_query_combines_patient_context(self):
        result = planned_context(
            "Why was my sugar tablet changed last time?",
            self.resources,
            intents=["medication", "result", "visit"],
            concepts=["metformin", "diabetes", "glucose", "HbA1c"],
            time_scope="latest",
        )
        relevant_ids = {item.event.resource_id for item in result.relevant_events}
        all_context_ids = relevant_ids | {
            event.resource_id for event in result.related_context
        }
        self.assertEqual(result.resolution_status, "resolved")
        self.assertIn("metformin", all_context_ids)
        self.assertIn("hba1c", all_context_ids)
        self.assertIn("enc-diabetes", all_context_ids)
        self.assertEqual(
            result.retrieval_modes, ["structured", "keyword", "fuzzy"]
        )

    def test_misspelled_drug_name_is_resolved_from_patient_record(self):
        result = planned_context(
            "Tell me about metfornin",
            self.resources,
            intents=["medication"],
            concepts=["metformin"],
        )
        self.assertEqual(result.plan.intent, "medication")
        self.assertEqual(result.resolution_status, "resolved")
        self.assertEqual(result.relevant_events[0].event.resource_id, "metformin")

    def test_generic_medication_query_requests_clarification(self):
        resources = [
            *self.resources,
            medication_resource("Lisinopril 10 MG Oral Tablet", "lisinopril"),
        ]
        result = planned_context(
            "What is my pill for?",
            resources,
            intents=["medication"],
            concepts=[],
        )
        self.assertEqual(result.resolution_status, "ambiguous")
        self.assertIn("Which medicine", result.clarification_question)
        self.assertIn("Metformin", result.clarification_question)
        self.assertIn("Lisinopril", result.clarification_question)

    def test_unknown_term_returns_recent_context_without_claiming_a_match(self):
        result = planned_context(
            "Tell me about xyznotfound",
            self.resources,
            intents=["general"],
            concepts=["xyznotfound"],
        )
        self.assertEqual(result.resolution_status, "not_found")
        self.assertTrue(result.relevant_events)
        self.assertEqual(result.relevant_events[0].score, 0.01)

        latest_result = planned_context(
            "Tell me about xyznotfound last time",
            self.resources,
            intents=["general"],
            concepts=["xyznotfound"],
            time_scope="latest",
        )
        self.assertEqual(latest_result.resolution_status, "not_found")

    def test_general_latest_request_uses_recent_record_context(self):
        result = planned_context(
            "What happened last time?",
            self.resources,
            intents=["general"],
            time_scope="latest",
            scope="broad",
        )
        self.assertEqual(result.resolution_status, "resolved")
        self.assertTrue(result.relevant_events)

    def test_lab_series_is_complete_even_when_ranked_results_are_capped(self):
        a1c_results = [
            observation_resource(f"a1c-{month}", f"2026-{month:02d}-01T09:00:00Z", 8.5 - month / 10)
            for month in range(1, 6)
        ]
        distractors = [
            observation_resource(
                f"glucose-{month}",
                f"2026-{month:02d}-02T09:00:00Z",
                100 + month,
                "2339-0",
                "Glucose",
            )
            for month in range(1, 8)
        ]
        result = planned_context(
            "How did my HbA1c change over time? Include every recorded HbA1c result.",
            [patient_resource(), *a1c_results, *distractors],
            intents=["result"],
            concepts=["HbA1c", "Hemoglobin A1c"],
            time_scope="historical",
            operation="lab_series",
            max_results=3,
        )

        self.assertEqual(result.deterministic_result.kind, "lab_series")
        self.assertEqual(result.deterministic_result.code, "4548-4")
        self.assertEqual(
            [event.resource_id for event in result.deterministic_result.events],
            [f"a1c-{month}" for month in range(1, 6)],
        )

    def test_current_medication_result_excludes_unrelated_patient_context(self):
        resources = [
            *self.resources,
            medication_resource("Lisinopril 10 MG Oral Tablet", "lisinopril"),
        ]
        result = planned_context(
            "What medications are currently recorded?",
            resources,
            intents=["medication"],
            time_scope="latest",
            operation="current_medications",
        )

        self.assertEqual(result.deterministic_result.kind, "current_medications")
        self.assertEqual(
            {event.resource_id for event in result.deterministic_result.events},
            {"metformin", "lisinopril"},
        )


if __name__ == "__main__":
    unittest.main()
