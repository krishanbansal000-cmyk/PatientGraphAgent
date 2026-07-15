"""Load additional Synthea data into the FHIR store.

Adds: AllergyIntolerance, Immunization, Procedure, CarePlan, and more
longitudinal Observation data (multiple lab draws over time).
"""

import json
import os

from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DATASET_ID = "myhealth-dataset"
FHIR_STORE_ID = "myhealth-fhir-store"
BASE_URL = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/fhirStores/{FHIR_STORE_ID}"

PATIENT_ID = "7c787a96-de6d-4a9d-88cc-94a15dc93aee"


def get_session():
    creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def create_resource(session, resource, resource_id=None):
    headers = {"Content-Type": "application/fhir+json"}
    rtype = resource["resourceType"]
    if resource_id:
        url = f"{BASE_URL}/fhir/{rtype}/{resource_id}"
        response = session.put(url, headers=headers, data=json.dumps(resource))
    else:
        url = f"{BASE_URL}/fhir/{rtype}"
        response = session.post(url, headers=headers, data=json.dumps(resource))
    if response.status_code not in (200, 201):
        print(f"  WARN: {rtype}/{resource_id} -> {response.status_code}: {response.text[:200]}")
    return response.json() if response.status_code in (200, 201) else None


def fhir_get(session, resource_type, params=None):
    url = f"{BASE_URL}/fhir/{resource_type}"
    headers = {"Accept": "application/fhir+json"}
    response = session.get(url, headers=headers, params=params)
    if response.status_code != 200:
        return {"entry": []}
    return response.json()


def main():
    print("Loading additional Synthea-style data into FHIR store...")
    session = get_session()

    # =========================================================================
    # 1. AllergyIntolerance resources
    # =========================================================================
    print("\n--- AllergyIntolerance ---")
    allergies = [
        {
            "resourceType": "AllergyIntolerance",
            "id": "allergy-penicillin",
            "clinicalStatus": {"coding": [{"system": "http://hl7.org/fhir/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://hl7.org/fhir/CodeSystem/allergyintolerance-verification", "code": "confirmed"}]},
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "764146007", "display": "Penicillin (substance)"}], "text": "Penicillin"},
            "patient": {"reference": f"Patient/{PATIENT_ID}"},
            "recordedDate": "2010-05-15T10:00:00+00:00",
            "reaction": [{"manifestation": [{"text": "Skin rash"}], "severity": "mild"}],
        },
        {
            "resourceType": "AllergyIntolerance",
            "id": "allergy-latex",
            "clinicalStatus": {"coding": [{"system": "http://hl7.org/fhir/CodeSystem/allergyintolerance-clinical", "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://hl7.org/fhir/CodeSystem/allergyintolerance-verification", "code": "confirmed"}]},
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "300916003", "display": "Latex (substance)"}], "text": "Latex"},
            "patient": {"reference": f"Patient/{PATIENT_ID}"},
            "recordedDate": "2015-03-20T14:00:00+00:00",
            "reaction": [{"manifestation": [{"text": "Contact dermatitis"}], "severity": "moderate"}],
        },
    ]
    for a in allergies:
        create_resource(session, a, resource_id=a["id"])
    print(f"Created {len(allergies)} AllergyIntolerance resources")

    # =========================================================================
    # 2. Immunization resources
    # =========================================================================
    print("\n--- Immunization ---")
    immunizations = [
        {"id": "imm-flu-2017", "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "140", "display": "Influenza, seasonal, injectable, preservative free"}], "text": "Influenza vaccine"}, "occurrenceDateTime": "2017-10-15T09:00:00+00:00"},
        {"id": "imm-flu-2018", "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "140", "display": "Influenza, seasonal, injectable, preservative free"}], "text": "Influenza vaccine"}, "occurrenceDateTime": "2018-10-15T09:00:00+00:00"},
        {"id": "imm-tdap-2015", "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "115", "display": "Tetanus and diphtheria toxoids and acellular pertussis vaccine"}], "text": "Tdap vaccine"}, "occurrenceDateTime": "2015-06-10T11:00:00+00:00"},
        {"id": "imm-pneumo-2016", "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "33", "display": "Pneumococcal polysaccharide vaccine"}], "text": "Pneumococcal vaccine"}, "occurrenceDateTime": "2016-11-20T10:00:00+00:00"},
        {"id": "imm-covid-2021", "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "207", "display": "COVID-19, mRNA, LNP-S, PF, 100 mcg/0.5 mL dose"}], "text": "COVID-19 vaccine"}, "occurrenceDateTime": "2021-03-01T14:00:00+00:00"},
    ]
    for imm in immunizations:
        resource = {
            "resourceType": "Immunization",
            "id": imm["id"],
            "status": "completed",
            "vaccineCode": imm["vaccineCode"],
            "patient": {"reference": f"Patient/{PATIENT_ID}"},
            "occurrenceDateTime": imm["occurrenceDateTime"],
            "primarySource": True,
        }
        create_resource(session, resource, resource_id=resource["id"])
    print(f"Created {len(immunizations)} Immunization resources")

    # =========================================================================
    # 3. Procedure resources
    # =========================================================================
    print("\n--- Procedure ---")
    procedures = [
        {"id": "proc-ekg-1", "code": {"coding": [{"system": "http://snomed.info/sct", "code": "40101002", "display": "Electrocardiographic procedure"}], "text": "EKG"}, "performedDateTime": "2018-10-04T15:00:00+00:00", "encounter": {"reference": "Encounter/enc-ed-1"}},
        {"id": "proc-ekg-2", "code": {"coding": [{"system": "http://snomed.info/sct", "code": "40101002", "display": "Electrocardiographic procedure"}], "text": "EKG"}, "performedDateTime": "2018-10-05T08:00:00+00:00", "encounter": {"reference": "Encounter/enc-inpatient-1"}},
        {"id": "proc-iv-1", "code": {"coding": [{"system": "http://snomed.info/sct", "code": "307492004", "display": "Intravenous infusion therapy"}], "text": "IV fluid therapy"}, "performedDateTime": "2018-10-04T16:00:00+00:00", "encounter": {"reference": "Encounter/enc-inpatient-1"}},
        {"id": "proc-glucose-check", "code": {"coding": [{"system": "http://snomed.info/sct", "code": "171204005", "display": "Blood glucose monitoring"}], "text": "Blood glucose monitoring"}, "performedDateTime": "2018-10-04T15:30:00+00:00", "encounter": {"reference": "Encounter/enc-ed-1"}},
    ]
    for p in procedures:
        resource = {
            "resourceType": "Procedure",
            "id": p["id"],
            "status": "completed",
            "code": p["code"],
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "performedDateTime": p["performedDateTime"],
            "encounter": p["encounter"],
        }
        create_resource(session, resource, resource_id=resource["id"])
    print(f"Created {len(procedures)} Procedure resources")

    # =========================================================================
    # 4. CarePlan resources
    # =========================================================================
    print("\n--- CarePlan ---")
    care_plans = [
        {
            "resourceType": "CarePlan",
            "id": "careplan-diabetes",
            "status": "active",
            "intent": "plan",
            "title": "Diabetes Management Plan",
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "created": "2018-10-01T09:30:00+00:00",
            "addresses": [{"reference": "Condition/b708d98d-ccec-4a8b-9c2c-72e226f71cc1"}],
            "activity": [
                {"detail": {"code": {"text": "Daily blood glucose monitoring"}, "status": "in-progress"}},
                {"detail": {"code": {"text": "Metformin 500mg daily"}, "status": "in-progress"}},
                {"detail": {"code": {"text": "HbA1c every 3 months"}, "status": "scheduled"}},
                {"detail": {"code": {"text": "Dietary counseling"}, "status": "scheduled"}},
            ],
        },
        {
            "resourceType": "CarePlan",
            "id": "careplan-hypertension",
            "status": "active",
            "intent": "plan",
            "title": "Hypertension Management Plan",
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "created": "2018-10-01T09:30:00+00:00",
            "addresses": [{"reference": "Condition/7d33fc5c-b368-4276-b3eb-f8fbc277571a"}],
            "activity": [
                {"detail": {"code": {"text": "Hydrochlorothiazide 25mg daily"}, "status": "in-progress"}},
                {"detail": {"code": {"text": "Blood pressure monitoring twice daily"}, "status": "in-progress"}},
                {"detail": {"code": {"text": "Sodium-restricted diet"}, "status": "in-progress"}},
                {"detail": {"code": {"text": "Follow-up in 3 months"}, "status": "scheduled"}},
            ],
        },
    ]
    for cp in care_plans:
        create_resource(session, cp, resource_id=cp["id"])
    print(f"Created {len(care_plans)} CarePlan resources")

    # =========================================================================
    # 5. Longitudinal Observations (multiple lab draws over 12 months)
    # =========================================================================
    print("\n--- Longitudinal Observations ---")
    # Create 4 additional lab draws at different dates
    lab_dates = [
        "2018-01-15T06:00:00+00:00",
        "2018-04-15T06:00:00+00:00",
        "2018-07-15T06:00:00+00:00",
        "2019-01-15T06:00:00+00:00",
    ]

    lab_templates = [
        {"loinc": "4548-4", "name": "Hemoglobin A1c/Hemoglobin.total in Blood", "short": "HbA1c", "unit": "%", "values": [7.8, 7.5, 7.4, 7.1]},
        {"loinc": "2339-0", "name": "Glucose", "short": "Glucose", "unit": "mg/dL", "values": [145.0, 132.0, 128.0, 110.0]},
        {"loinc": "3094-0", "name": "Urea nitrogen in Serum or Plasma", "short": "BUN", "unit": "mg/dL", "values": [18.0, 17.0, 19.0, 16.0]},
        {"loinc": "33914-3", "name": "Estimated Glomerular Filtration Rate", "short": "eGFR", "unit": "mL/min/{1.73_m2}", "values": [85.0, 88.0, 87.0, 91.0]},
        {"loinc": "2093-3", "name": "Cholesterol", "short": "Total Cholesterol", "unit": "mg/dL", "values": [225.0, 218.0, 215.0, 205.0]},
        {"loinc": "2085-9", "name": "High Density Lipoprotein Cholesterol", "short": "HDL", "unit": "mg/dL", "values": [52.0, 55.0, 56.0, 60.0]},
        {"loinc": "18262-6", "name": "Low Density Lipoprotein Cholesterol", "short": "LDL", "unit": "mg/dL", "values": [130.0, 125.0, 120.0, 112.0]},
        {"loinc": "2571-8", "name": "Triglyceride", "short": "Triglycerides", "unit": "mg/dL", "values": [210.0, 195.0, 190.0, 175.0]},
    ]

    obs_count = 0
    for date_idx, date_str in enumerate(lab_dates):
        for lab in lab_templates:
            value = lab["values"][date_idx]
            obs_id = f"obs-longitudinal-{lab['loinc']}-{date_idx+1}"
            resource = {
                "resourceType": "Observation",
                "id": obs_id,
                "status": "final",
                "code": {
                    "coding": [{"system": "http://loinc.org", "code": lab["loinc"], "display": lab["name"]}],
                    "text": lab["short"],
                },
                "subject": {"reference": f"Patient/{PATIENT_ID}"},
                "effectiveDateTime": date_str,
                "valueQuantity": {"value": value, "unit": lab["unit"]},
            }
            create_resource(session, resource, resource_id=resource["id"])
            obs_count += 1
    print(f"Created {obs_count} longitudinal Observation resources across {len(lab_dates)} dates")

    # =========================================================================
    # 6. Blood pressure observations (vital signs)
    # =========================================================================
    print("\n--- Blood Pressure Observations ---")
    bp_dates = [
        "2018-01-15T09:00:00+00:00", "2018-04-15T09:00:00+00:00",
        "2018-07-15T09:00:00+00:00", "2018-10-01T09:15:00+00:00",
        "2018-10-03T11:15:00+00:00", "2018-10-04T14:15:00+00:00",
        "2019-01-15T09:00:00+00:00",
    ]
    bp_values = [
        (148, 92), (142, 88), (138, 86), (135, 84),
        (130, 82), (145, 90), (128, 80),
    ]
    bp_count = 0
    for i, ((systolic, diastolic), date_str) in enumerate(zip(bp_values, bp_dates)):
        obs_id = f"obs-bp-{i+1}"
        resource = {
            "resourceType": "Observation",
            "id": obs_id,
            "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
            "code": {
                "coding": [{"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel with all children optional"}],
                "text": "Blood pressure",
            },
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "effectiveDateTime": date_str,
            "component": [
                {"code": {"coding": [{"system": "http://loinc.org", "code": "8480-6", "display": "Systolic blood pressure"}], "text": "Systolic"}, "valueQuantity": {"value": systolic, "unit": "mm[Hg]"}},
                {"code": {"coding": [{"system": "http://loinc.org", "code": "8462-4", "display": "Diastolic blood pressure"}], "text": "Diastolic"}, "valueQuantity": {"value": diastolic, "unit": "mm[Hg]"}},
            ],
        }
        create_resource(session, resource, resource_id=resource["id"])
        bp_count += 1
    print(f"Created {bp_count} Blood Pressure Observations")

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n--- Final Summary ---")
    for rtype in ["Patient", "Condition", "MedicationRequest", "Observation", "Encounter", "Practitioner", "Organization", "AllergyIntolerance", "Immunization", "Procedure", "CarePlan"]:
        bundle = fhir_get(session, rtype, {"_count": "1000"})
        count = len(bundle.get("entry", []))
        print(f"  {rtype}: {count}")

    print("\nDone. FHIR store now has richer data for testing.")


if __name__ == "__main__":
    main()
