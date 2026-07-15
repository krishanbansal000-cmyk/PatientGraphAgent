from agent.patient_journey import build_patient_journey


PATIENT_ID = "john"


def patient():
    return {
        "resourceType": "Patient",
        "id": PATIENT_ID,
        "birthDate": "1970-01-01",
    }


def encounter():
    return {
        "resourceType": "Encounter",
        "id": "visit-june",
        "status": "finished",
        "class": {"display": "Primary care visit"},
        "period": {"start": "2026-06-01T09:00:00Z"},
        "meta": {"versionId": "2", "lastUpdated": "2026-06-01T12:00:00Z"},
    }


def condition():
    return {
        "resourceType": "Condition",
        "id": "diabetes",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {
            "text": "Type 2 diabetes",
            "coding": [{"system": "http://snomed.info/sct", "code": "44054006"}],
        },
        "encounter": {"reference": "Encounter/visit-june"},
        "onsetDateTime": "2026-06-01",
    }


def medication():
    return {
        "resourceType": "MedicationRequest",
        "id": "metformin",
        "status": "active",
        "medicationCodeableConcept": {
            "text": "Metformin ER 500 mg",
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "860975"}],
        },
        "encounter": {"reference": "Encounter/visit-june"},
        "authoredOn": "2026-06-01",
    }


def observation(resource_id, date, value):
    return {
        "resourceType": "Observation",
        "id": resource_id,
        "status": "final",
        "code": {
            "text": "HbA1c",
            "coding": [{"system": "http://loinc.org", "code": "4548-4"}],
        },
        "effectiveDateTime": date,
        "valueQuantity": {"value": value, "unit": "%"},
    }


def care_plan():
    return {
        "resourceType": "CarePlan",
        "id": "diabetes-plan",
        "status": "active",
        "title": "Diabetes management",
        "created": "2018-10-01T09:30:00Z",
        "meta": {"lastUpdated": "2026-07-12T08:00:00Z"},
    }


def immunization():
    return {
        "resourceType": "Immunization",
        "id": "flu-2018",
        "status": "completed",
        "vaccineCode": {"text": "Seasonal influenza vaccine"},
        "occurrenceDateTime": "2018-10-02T10:30:00Z",
    }


def test_groups_encounter_linked_resources_and_preserves_citations():
    journey = build_patient_journey(
        PATIENT_ID,
        [patient(), encounter(), condition(), medication()],
    )

    assert len(journey.episodes) == 1
    episode = journey.episodes[0]
    assert episode.type == "encounter"
    assert {item.reference for item in episode.items} == {
        "Encounter/visit-june",
        "Condition/diabetes",
        "MedicationRequest/metformin",
    }
    citation = next(item for item in episode.citations if item.reference == "Encounter/visit-june")
    assert citation.version == "2"


def test_builds_current_state_from_active_fhir_statuses():
    journey = build_patient_journey(
        PATIENT_ID,
        [patient(), encounter(), condition(), medication()],
    )

    assert [item.display for item in journey.current_state.active_conditions] == ["Type 2 diabetes"]
    assert [item.display for item in journey.current_state.current_medications] == ["Metformin ER 500 mg"]
    assert journey.group_id.startswith("patient_")
    assert PATIENT_ID not in journey.group_id


def test_standalone_results_are_grouped_by_date_and_report_change():
    journey = build_patient_journey(
        PATIENT_ID,
        [
            patient(),
            observation("a1c-june", "2026-06-01", 7.4),
            observation("a1c-july", "2026-07-01", 8.1),
        ],
    )

    assert len(journey.episodes) == 2
    july = journey.episodes[0]
    assert july.type == "result"
    assert july.changes[0].kind == "increased"
    assert "7.4 % to 8.1 %" in july.changes[0].label


def test_filters_by_episode_type_and_date():
    journey = build_patient_journey(
        PATIENT_ID,
        [
            patient(),
            encounter(),
            condition(),
            medication(),
            observation("a1c-july", "2026-07-01", 8.1),
        ],
        date_start="2026-07-01",
        episode_types=["result"],
    )

    assert len(journey.episodes) == 1
    assert journey.episodes[0].type == "result"


def test_care_plan_uses_created_date_before_last_updated_and_has_clear_name():
    journey = build_patient_journey(PATIENT_ID, [patient(), care_plan()])

    assert len(journey.episodes) == 1
    episode = journey.episodes[0]
    assert episode.type == "care_plan"
    assert episode.date == "2018-10-01T09:30:00Z"
    assert episode.title == "Care plan: Diabetes management"
    assert episode.category_counts == {"care_plan": 1}


def test_immunization_has_distinct_episode_type_and_name():
    journey = build_patient_journey(PATIENT_ID, [patient(), immunization()])

    assert len(journey.episodes) == 1
    episode = journey.episodes[0]
    assert episode.type == "immunization"
    assert episode.title == "Immunization: Seasonal influenza vaccine"
    assert episode.category_counts == {"immunization": 1}


def test_category_filter_includes_matching_items_inside_encounter_episode():
    medication_journey = build_patient_journey(
        PATIENT_ID,
        [patient(), encounter(), condition(), medication()],
        episode_types=["medication"],
    )
    condition_journey = build_patient_journey(
        PATIENT_ID,
        [patient(), encounter(), condition(), medication()],
        episode_types=["condition"],
    )

    assert [episode.type for episode in medication_journey.episodes] == ["encounter"]
    assert medication_journey.episodes[0].category_counts["medication"] == 1
    assert [episode.type for episode in condition_journey.episodes] == ["encounter"]
    assert condition_journey.episodes[0].category_counts["condition"] == 1
