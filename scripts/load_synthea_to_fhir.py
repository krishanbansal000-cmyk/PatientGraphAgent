"""
Load the Synthea sample patient from BigQuery into the Cloud Healthcare FHIR store.
This script reconstructs FHIR R4 resources from the parsed BigQuery tables.
"""

import json
import os
import uuid
from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery

# Configuration
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DATASET_ID = "myhealth-dataset"
FHIR_STORE_ID = "myhealth-fhir-store"
BASE_URL = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{FHIR_STORE_ID}"

PATIENT_ID = "7c787a96-de6d-4a9d-88cc-94a15dc93aee"


def get_authorized_session():
    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(credentials)


def create_resource(session, resource, resource_id=None):
    headers = {"Content-Type": "application/fhir+json"}
    if resource_id:
        # Use PUT to create/update with a specific resource ID
        url = f"{BASE_URL}/fhir/{resource['resourceType']}/{resource_id}"
        response = session.put(url, headers=headers, data=json.dumps(resource))
    else:
        # Let the server assign the ID
        url = f"{BASE_URL}/fhir/{resource['resourceType']}"
        response = session.post(url, headers=headers, data=json.dumps(resource))
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create {resource['resourceType']}: {response.status_code} {response.text}")
    return response.json()


def build_patient_resource(patient_row):
    return {
        "resourceType": "Patient",
        "id": patient_row["id"],
        "gender": patient_row["gender"],
        "birthDate": patient_row["birthDate"],
    }


def build_condition_resource(condition_row, patient_id):
    return {
        "resourceType": "Condition",
        "id": str(uuid.uuid4()),
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": condition_row["clinical_status"].lower() if condition_row["clinical_status"] else "active",
                }
            ]
        },
        "code": {
            "coding": [
                {
                    "system": condition_row["system"] or "http://snomed.info/sct",
                    "code": condition_row["code"],
                    "display": condition_row["condition_name"],
                }
            ],
            "text": condition_row["condition_name"],
        },
        "subject": {"reference": f"Patient/{patient_id}"},
    }


def build_medication_request_resource(medication_row, patient_id):
    return {
        "resourceType": "MedicationRequest",
        "id": str(uuid.uuid4()),
        "status": medication_row["status"].lower() if medication_row["status"] else "active",
        "intent": "order",
        "medicationCodeableConcept": {
            "coding": [
                {
                    "system": medication_row["system"] or "http://www.nlm.nih.gov/research/umls/rxnorm",
                    "code": medication_row["code"],
                    "display": medication_row["medication_name"],
                }
            ],
            "text": medication_row["medication_name"],
        },
        "subject": {"reference": f"Patient/{patient_id}"},
    }


def build_observation_resource(observation_row, patient_id):
    return {
        "resourceType": "Observation",
        "id": str(uuid.uuid4()),
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "laboratory",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": observation_row["loinc_system"] or "http://loinc.org",
                    "code": observation_row["loinc_code"],
                    "display": observation_row["observation_name"],
                }
            ],
            "text": observation_row["observation_name"],
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": observation_row["date"],
        "valueQuantity": {
            "value": observation_row["value"],
            "unit": observation_row["unit"],
            "system": "http://unitsofmeasure.org",
            "code": observation_row["unit"],
        },
    }


def fetch_patient_data(bq_client, patient_id):
    patient_sql = f"""
        SELECT id, gender, birthDate
        FROM `myhealth_datasets.john_patient`
        WHERE id = '{patient_id}'
        LIMIT 1
    """
    patient_rows = list(bq_client.query(patient_sql).result())

    conditions_sql = f"""
        SELECT
            c.code.text AS condition_name,
            coding.code,
            coding.system,
            c.clinicalStatus AS clinical_status
        FROM `myhealth_datasets.john_conditions` c,
        UNNEST(c.code.coding) AS coding
        WHERE c.subject.patientId = '{patient_id}'
    """
    condition_rows = list(bq_client.query(conditions_sql).result())

    medications_sql = f"""
        SELECT
            m.medication.codeableConcept.text AS medication_name,
            coding.code,
            coding.system,
            m.status
        FROM `myhealth_datasets.john_medications` m,
        UNNEST(m.medication.codeableConcept.coding) AS coding
        WHERE m.subject.patientId = '{patient_id}'
        ORDER BY m.authoredOn DESC
    """
    medication_rows = list(bq_client.query(medications_sql).result())

    observations_sql = f"""
        SELECT
            o.code.text AS observation_name,
            coding.code AS loinc_code,
            coding.system AS loinc_system,
            o.value.quantity.value AS value,
            o.value.quantity.unit AS unit,
            o.effective.dateTime AS date
        FROM `myhealth_datasets.john_observations` o,
        UNNEST(o.code.coding) AS coding
        WHERE o.subject.patientId = '{patient_id}'
          AND o.value.quantity IS NOT NULL
        ORDER BY o.effective.dateTime DESC
        LIMIT 20
    """
    observation_rows = list(bq_client.query(observations_sql).result())

    return patient_rows, condition_rows, medication_rows, observation_rows


def main():
    print("Loading Synthea patient data into Cloud Healthcare FHIR store...")
    bq_client = bigquery.Client(project=PROJECT_ID)
    session = get_authorized_session()

    patient_rows, condition_rows, medication_rows, observation_rows = fetch_patient_data(
        bq_client, PATIENT_ID
    )

    if not patient_rows:
        raise RuntimeError(f"Patient {PATIENT_ID} not found in BigQuery")

    # Create patient with a specific ID using PUT
    patient_resource = build_patient_resource(dict(patient_rows[0]))
    create_resource(session, patient_resource, resource_id=PATIENT_ID)
    print(f"Created Patient/{PATIENT_ID}")

    # Create conditions
    for row in condition_rows:
        condition_resource = build_condition_resource(dict(row), PATIENT_ID)
        create_resource(session, condition_resource)
    print(f"Created {len(condition_rows)} Condition resources")

    # Create medications
    for row in medication_rows:
        medication_resource = build_medication_request_resource(dict(row), PATIENT_ID)
        create_resource(session, medication_resource)
    print(f"Created {len(medication_rows)} MedicationRequest resources")

    # Create observations
    for row in observation_rows:
        observation_resource = build_observation_resource(dict(row), PATIENT_ID)
        create_resource(session, observation_resource)
    print(f"Created {len(observation_rows)} Observation resources")

    print("Done. FHIR store is loaded.")


if __name__ == "__main__":
    main()
