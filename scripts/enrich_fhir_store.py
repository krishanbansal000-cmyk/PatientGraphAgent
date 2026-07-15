"""
Enrich the FHIR store with Practitioners, Organizations, and Encounters.
Then update existing Conditions, MedicationRequests, and Observations to reference them.
"""

import json
import os

from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession

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
        url = f"{BASE_URL}/fhir/{resource['resourceType']}/{resource_id}"
        response = session.put(url, headers=headers, data=json.dumps(resource))
    else:
        url = f"{BASE_URL}/fhir/{resource['resourceType']}"
        response = session.post(url, headers=headers, data=json.dumps(resource))
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create {resource['resourceType']}: {response.status_code} {response.text}")
    return response.json()


def fhir_get(session, resource_type, params=None):
    url = f"{BASE_URL}/fhir/{resource_type}"
    headers = {"Accept": "application/fhir+json"}
    response = session.get(url, headers=headers, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"FHIR API error: {response.status_code} {response.text}")
    return response.json()


def update_resource(session, resource_type, resource_id, resource):
    headers = {"Content-Type": "application/fhir+json"}
    url = f"{BASE_URL}/fhir/{resource_type}/{resource_id}"
    response = session.put(url, headers=headers, data=json.dumps(resource))
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Failed to update {resource_type}/{resource_id}: {response.status_code} {response.text}")
    return response.json()


def build_practitioner(resource_id, family, given, qualification, prefix=None, phone=None):
    name = {"family": family, "given": [given]}
    if prefix:
        name["prefix"] = [prefix]
    return {
        "resourceType": "Practitioner",
        "id": resource_id,
        "name": [name],
        "qualification": [{"code": {"text": qualification}}],
        "telecom": [{"system": "phone", "value": phone or "555-0000"}] if phone else []
    }


def build_organization(resource_id, name, type_code):
    return {
        "resourceType": "Organization",
        "id": resource_id,
        "name": name,
        "type": [{"coding": [{"system": "http://hl7.org/fhir/organization-type", "code": type_code}]}]
    }


def build_encounter(resource_id, status, class_code, class_display, start, end, participant_refs, reason_display=None, service_provider_ref=None):
    encounter = {
        "resourceType": "Encounter",
        "id": resource_id,
        "status": status,
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": class_code, "display": class_display},
        "subject": {"reference": f"Patient/{PATIENT_ID}"},
        "period": {"start": start, "end": end},
        "participant": [{"individual": {"reference": ref}} for ref in participant_refs],
        "reasonCode": [{"text": reason_display}] if reason_display else [],
        "serviceProvider": {"reference": service_provider_ref} if service_provider_ref else None
    }
    return encounter


def main():
    print("Enriching FHIR store with Practitioners, Organizations, and Encounters...")
    session = get_authorized_session()

    # Create practitioners
    practitioners = [
        build_practitioner("pract-pcp", "Martinez", "Elena", "MD - Primary Care", prefix="Dr."),
        build_practitioner("pract-endo", "Chen", "Robert", "MD - Endocrinology", prefix="Dr."),
        build_practitioner("pract-cardio", "Singh", "Priya", "MD - Cardiology", prefix="Dr."),
        build_practitioner("pract-ed", "Williams", "James", "MD - Emergency Medicine", prefix="Dr."),
        build_practitioner("pract-lab", "Johnson", "Lisa", "MT - Medical Technologist"),
    ]
    for p in practitioners:
        create_resource(session, p, resource_id=p["id"])
    print(f"Created {len(practitioners)} Practitioner resources")

    # Create organizations
    organizations = [
        build_organization("org-pcp-clinic", "Maple Street Primary Care", "prov"),
        build_organization("org-hospital", "Regional Medical Center", "prov"),
        build_organization("org-lab", "QuestWest Laboratory", "prov"),
    ]
    for o in organizations:
        create_resource(session, o, resource_id=o["id"])
    print(f"Created {len(organizations)} Organization resources")

    # Create encounters
    encounters = [
        build_encounter(
            "enc-pcp-1", "finished", "AMB", "ambulatory",
            "2018-10-01T09:00:00+00:00", "2018-10-01T09:30:00+00:00",
            ["Practitioner/pract-pcp"], "Routine diabetes and hypertension follow-up",
            "Organization/org-pcp-clinic"
        ),
        build_encounter(
            "enc-endo-1", "finished", "AMB", "ambulatory",
            "2018-10-02T10:00:00+00:00", "2018-10-02T10:45:00+00:00",
            ["Practitioner/pract-endo"], "Endocrinology consultation for diabetes management",
            "Organization/org-hospital"
        ),
        build_encounter(
            "enc-cardio-1", "finished", "AMB", "ambulatory",
            "2018-10-03T11:00:00+00:00", "2018-10-03T11:30:00+00:00",
            ["Practitioner/pract-cardio"], "Cardiology consultation for hypertension",
            "Organization/org-hospital"
        ),
        build_encounter(
            "enc-ed-1", "finished", "EMER", "emergency",
            "2018-10-04T14:00:00+00:00", "2018-10-04T18:00:00+00:00",
            ["Practitioner/pract-ed"], "Chest pain and hyperglycemia",
            "Organization/org-hospital"
        ),
        build_encounter(
            "enc-inpatient-1", "finished", "IMP", "inpatient",
            "2018-10-04T18:00:00+00:00", "2018-10-06T10:00:00+00:00",
            ["Practitioner/pract-endo", "Practitioner/pract-cardio"], "Hospitalization for hyperglycemic crisis",
            "Organization/org-hospital"
        ),
        build_encounter(
            "enc-lab-1", "finished", "AMB", "ambulatory",
            "2018-10-02T06:00:00+00:00", "2018-10-02T06:30:00+00:00",
            ["Practitioner/pract-lab"], "Laboratory draw",
            "Organization/org-lab"
        ),
    ]
    for e in encounters:
        # Remove None values
        e = {k: v for k, v in e.items() if v is not None}
        create_resource(session, e, resource_id=e["id"])
    print(f"Created {len(encounters)} Encounter resources")

    # Fetch existing patient resources
    conditions_bundle = fhir_get(session, "Condition", {"patient": f"Patient/{PATIENT_ID}"})
    medications_bundle = fhir_get(session, "MedicationRequest", {"patient": f"Patient/{PATIENT_ID}"})
    observations_bundle = fhir_get(session, "Observation", {"patient": f"Patient/{PATIENT_ID}"})

    # Update conditions to reference an encounter
    for i, entry in enumerate(conditions_bundle.get("entry", [])):
        resource = entry.get("resource", {})
        resource_id = resource.get("id")
        if not resource_id:
            continue
        # Assign conditions to PCP or ED encounter
        if i < 3:
            resource["encounter"] = {"reference": "Encounter/enc-pcp-1"}
        elif i < 6:
            resource["encounter"] = {"reference": "Encounter/enc-endo-1"}
        else:
            resource["encounter"] = {"reference": "Encounter/enc-cardio-1"}
        # Add recorder
        resource["recorder"] = {"reference": "Practitioner/pract-pcp"}
        update_resource(session, "Condition", resource_id, resource)
    print(f"Updated {len(conditions_bundle.get('entry', []))} Condition resources with encounter references")

    # Update medications to reference an encounter and requester
    for entry in medications_bundle.get("entry", []):
        resource = entry.get("resource", {})
        resource_id = resource.get("id")
        if not resource_id:
            continue
        resource["encounter"] = {"reference": "Encounter/enc-pcp-1"}
        resource["requester"] = {"reference": "Practitioner/pract-pcp"}
        update_resource(session, "MedicationRequest", resource_id, resource)
    print(f"Updated {len(medications_bundle.get('entry', []))} MedicationRequest resources with encounter and requester references")

    # Update observations to reference an encounter and performer
    for entry in observations_bundle.get("entry", []):
        resource = entry.get("resource", {})
        resource_id = resource.get("id")
        if not resource_id:
            continue
        resource["encounter"] = {"reference": "Encounter/enc-lab-1"}
        resource["performer"] = [{"reference": "Practitioner/pract-lab"}]
        update_resource(session, "Observation", resource_id, resource)
    print(f"Updated {len(observations_bundle.get('entry', []))} Observation resources with encounter and performer references")

    print("Done. FHIR store is enriched with related practitioners, organizations, and encounters.")


if __name__ == "__main__":
    main()
