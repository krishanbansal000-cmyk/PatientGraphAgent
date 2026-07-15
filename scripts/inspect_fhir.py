"""Inspect the actual FHIR data to evaluate graph quality."""
import json
from collections import Counter

from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession

PROJECT = "avinia-app"
LOCATION = "us-central1"
DATASET = "myhealth-dataset"
FHIR_STORE = "myhealth-fhir-store"
PATIENT_ID = "7c787a96-de6d-4a9d-88cc-94a15dc93aee"
FHIR_BASE = f"https://healthcare.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}/datasets/{DATASET}/fhirStores/{FHIR_STORE}/fhir"

creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
session = AuthorizedSession(creds)

# Get Patient/$everything
url = f"{FHIR_BASE}/Patient/{PATIENT_ID}/$everything"
headers = {"Accept": "application/fhir+json"}
resources = []
while url and len(resources) < 500:
    resp = session.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"Error: {resp.status_code}")
        break
    bundle = resp.json()
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        if r.get("resourceType") and r.get("id"):
            resources.append(r)
    next_url = None
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            next_url = link.get("url")
            break
    url = next_url

print(f"Total FHIR resources: {len(resources)}")
print()

# Count by resource type
type_counts = Counter(r["resourceType"] for r in resources)
print("=== RESOURCE TYPE COUNTS ===")
for rtype, count in type_counts.most_common():
    print(f"  {rtype}: {count}")

# Show conditions with full detail
print()
print("=== CONDITIONS (full detail) ===")
for r in resources:
    if r["resourceType"] != "Condition":
        continue
    code = r.get("code", {})
    coding = code.get("coding", [{}])[0]
    clinical_status = r.get("clinicalStatus", {})
    status = clinical_status.get("coding", [{}])[0].get("code", "") if isinstance(clinical_status, dict) else str(clinical_status)
    print(f"  ID: {r['id']}")
    print(f"    Name: {code.get('text', 'unnamed')}")
    print(f"    Code: {coding.get('code')} ({coding.get('system')})")
    print(f"    Status: {status}")
    print(f"    Onset: {r.get('onsetDateTime', 'N/A')}")
    print(f"    Recorded: {r.get('recordedDate', 'N/A')}")
    enc = r.get("encounter", {})
    print(f"    Encounter: {enc.get('reference', 'N/A')}")
    # Check for asserter (who diagnosed)
    asserter = r.get("asserter", {})
    if asserter.get("reference"):
        print(f"    Asserted by: {asserter.get('reference')}")
    # Check for evidence/symptoms
    evidence = r.get("evidence", [])
    if evidence:
        print(f"    Evidence: {json.dumps(evidence[:2])}")
    print()

# Show medications with full detail
print("=== MEDICATIONS (full detail) ===")
for r in resources:
    if r["resourceType"] not in ("MedicationRequest", "MedicationStatement", "MedicationDispense"):
        continue
    med_cc = r.get("medicationCodeableConcept", {})
    coding = med_cc.get("coding", [{}])[0]
    print(f"  ID: {r['id']}")
    print(f"    Name: {med_cc.get('text', 'unnamed')}")
    print(f"    Code: {coding.get('code')} ({coding.get('system')})")
    print(f"    Status: {r.get('status')}")
    print(f"    Intent: {r.get('intent', 'N/A')}")
    print(f"    Authored: {r.get('authoredOn', 'N/A')}")
    enc = r.get("encounter", {})
    print(f"    Encounter: {enc.get('reference', 'N/A')}")
    # Dosage
    dosage = r.get("dosageInstruction", [])
    if dosage:
        d = dosage[0]
        print(f"    Dosage: {d.get('text', 'N/A')}")
        route = d.get("route", {})
        if route.get("text"):
            print(f"    Route: {route.get('text')}")
        freq = d.get("timing", {})
        if freq.get("repeat", {}).get("frequency"):
            print(f"    Frequency: {freq['repeat']['frequency']}x per {freq['repeat'].get('period')} {freq['repeat'].get('periodUnit')}")
    # Reason for prescription
    reason = r.get("reasonCode", [])
    if reason:
        print(f"    Reason: {reason[0].get('text', json.dumps(reason[0]))}")
    reason_ref = r.get("reasonReference", [])
    if reason_ref:
        print(f"    Reason ref: {[r.get('reference') for r in reason_ref]}")
    print()

# Show procedures
print("=== PROCEDURES ===")
for r in resources:
    if r["resourceType"] != "Procedure":
        continue
    code = r.get("code", {})
    coding = code.get("coding", [{}])[0]
    print(f"  ID: {r['id']}")
    print(f"    Name: {code.get('text', 'unnamed')}")
    print(f"    Code: {coding.get('code')} ({coding.get('system')})")
    print(f"    Status: {r.get('status')}")
    print(f"    Date: {r.get('performedDateTime', r.get('performedPeriod', {}).get('start', 'N/A'))}")
    enc = r.get("encounter", {})
    print(f"    Encounter: {enc.get('reference', 'N/A')}")
    print()

# Show allergies
print("=== ALLERGIES ===")
for r in resources:
    if r["resourceType"] != "AllergyIntolerance":
        continue
    code = r.get("code", {})
    coding = code.get("coding", [{}])[0]
    print(f"  ID: {r['id']}")
    print(f"    Allergen: {code.get('text', 'unnamed')}")
    print(f"    Code: {coding.get('code')} ({coding.get('system')})")
    print(f"    Status: {r.get('clinicalStatus', {}).get('coding', [{}])[0].get('code', 'N/A')}")
    print(f"    Criticality: {r.get('criticality', 'N/A')}")
    print(f"    Onset: {r.get('onsetDateTime', 'N/A')}")
    print()

# Show care plans
print("=== CARE PLANS ===")
for r in resources:
    if r["resourceType"] != "CarePlan":
        continue
    print(f"  ID: {r['id']}")
    print(f"    Title: {r.get('title', 'N/A')}")
    print(f"    Status: {r.get('status')}")
    print(f"    Category: {[c.get('text') for c in r.get('category', [])]}")
    print(f"    Addresses: {[a.get('reference') for a in r.get('addresses', [])]}")
    print()

# Show encounters with full detail
print("=== ENCOUNTERS (full detail) ===")
for r in resources:
    if r["resourceType"] != "Encounter":
        continue
    cls = r.get("class", {})
    print(f"  ID: {r['id']}")
    print(f"    Class: {cls.get('display', cls.get('code', 'N/A'))}")
    print(f"    Status: {r.get('status')}")
    print(f"    Period: {r.get('period', {})}")
    reason = r.get("reasonCode", [])
    if reason:
        print(f"    Reason: {reason[0].get('text', 'N/A')}")
    diag = r.get("diagnosis", [])
    if diag:
        for d in diag:
            print(f"    Diagnosis: {d.get('condition', {}).get('reference')} (rank {d.get('rank', 'N/A')})")
    print()

# Check for any symptom-related resources
print("=== CHECKING FOR SYMPTOMS ===")
symptom_keywords = ["symptom", "finding", "complaint", "pain", "nausea", "fatigue", "fever"]
symptom_found = []
for r in resources:
    rtype = r.get("resourceType", "")
    text = json.dumps(r).lower()
    for kw in symptom_keywords:
        if kw in text:
            symptom_found.append((rtype, r.get("id"), kw))
            break
if symptom_found:
    print(f"  Found {len(symptom_found)} resources with symptom-related terms:")
    for rtype, rid, kw in symptom_found[:10]:
        print(f"    [{rtype}] {rid} (matched: {kw})")
else:
    print("  No symptom-specific resources found in FHIR data.")

# Check observation categories
print()
print("=== OBSERVATION CATEGORIES ===")
obs_categories = Counter()
for r in resources:
    if r["resourceType"] != "Observation":
        continue
    cats = r.get("category", [])
    for cat in cats:
        coding = cat.get("coding", [{}])[0]
        obs_categories[coding.get("display", coding.get("code", "unknown"))] += 1
for cat, count in obs_categories.most_common():
    print(f"  {cat}: {count}")
