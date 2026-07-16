# Avinia GCP Prototype

MyHealth is a patient-facing health assistant for synthetic-data prototyping.
It combines Google Cloud Healthcare API FHIR retrieval, an ADK agent, an
optional Neo4j/Graphiti patient-memory graph, and an Alpine.js interface.

Detailed UML, graph, data-contract, ingestion, and retrieval
diagrams are in [docs/architecture.md](docs/architecture.md).

## Patient question retrieval

The ADK agent uses `search_patient_context` as its primary patient-record tool.
Gemini first supplies a schema-validated `PatientQueryPlan` in the tool call:
clinical intents, normalized concepts, time scope, ISO date boundaries, answer
scope, prior-turn references, and any exact operation. The backend derives safe
FHIR resource categories from those intents and runs structured search plus
supporting text/fuzzy recall. Rankings are fused and expanded with same-encounter,
referenced, and nearby clinical events before the agent answers. Focused FHIR
reads and medication knowledge tools remain available when the consolidated
result identifies a specific detail that needs more retrieval.

Exact lab-series and current-medication questions use deterministic result
operators inside the consolidated search. These operators return complete,
ordered FHIR evidence before the model explains it, avoiding generic top-N
omissions and unrelated citations.

The browser reuses one ADK session for the chat. The tool records the FHIR
resources discussed on each turn, allowing follow-ups such as "these problems"
or "that medicine" to reuse the cited patient context. Gemini resolves natural
language date windows into ISO boundaries; Pydantic validates them before the
backend applies deterministic date filtering. Timeline operations return a
chronological evidence set.

Every response includes a deterministic patient snapshot built from current
FHIR resources. Clinical onset/effective dates, recorded dates, and FHIR
last-updated timestamps remain separate so the agent does not describe a record
creation date as the start of a condition.

Misspelled medication names are resolved against medications actually present
in the patient record. Ambiguous medication requests return a clarification
question, and unmatched terms are marked `not_found` rather than inferred from
unrelated recent data.

## Medical literature evidence

The agent can search PubMed on demand through NCBI E-utilities. It returns
structured article metadata and source links for the UI citation list. Patient
facts are retrieved from FHIR first; PubMed searches contain only de-identified
clinical concepts, interventions, and outcomes. Literature remains
population-level supporting evidence and is not written into the patient graph.

## Terminology enrichment

`search_patient_context` batch-validates codes already present in its FHIR
evidence. RxNorm uses the version-pinned Google BigQuery public table
`bigquery-public-data.nlm_rxnorm.rxnconso_07_26`. ICD-10-CM uses the official
CDC April 1, 2026 release loaded into
`avinia-app.medical_terminology.icd10cm_2026`:

```powershell
python scripts/load_icd10cm.py --project avinia-app
```

The result includes the canonical display, dataset, release version, exact
match method, and structured source metadata. SNOMED CT and LOINC codes from
FHIR are retained as source-coded concepts; no external mapping is inferred.
A licensed/current project-owned release is required before validating those
vocabularies in bulk.

## Graphiti semantic memory

Cloud Healthcare FHIR is the only clinical source of truth. Exact conditions,
medications, observations, dates, statuses, and timelines are retrieved from
FHIR. Neo4j contains Graphiti's patient-partitioned temporal memory and a small
provenance bridge:

```text
Saga -[:HAS_EPISODE]-> Episodic -[:MENTIONS]-> Entity
Entity -[:RELATES_TO]-> Entity
Episodic -[:DERIVED_FROM]-> FHIRSource
```

### Graph nodes

| Node | Purpose |
|---|---|
| `Saga` | Patient-scoped journey container that orders memory episodes |
| `Episodic` | Bounded temporal memory created from one patient episode |
| `FHIRSource` | Provenance pointer to an authoritative FHIR resource |
| `PatientRecordSubject` | Non-identifying patient entity inside the isolated graph partition |
| `ClinicalEncounter` | Visit, emergency presentation, admission, or other encounter |
| `ClinicalCondition` | Recorded diagnosis, problem, or condition |
| `MedicationTherapy` | Medication order, statement, dispense, or administration |
| `ClinicalObservation` | Laboratory result, vital sign, measurement, or assessment |
| `PatientReportedSymptom` | Recorded symptom or complaint |
| `ClinicalProcedure` | Diagnostic, therapeutic, or preventive procedure |
| `ClinicalCarePlan` | Care plan, goal, or planned clinical activity |
| `ClinicalAllergy` | Allergy or intolerance |
| `ClinicalImmunization` | Recorded vaccine administration |

The clinical nodes are Graphiti `Entity` nodes with the corresponding typed
label and properties constrained by `clinical_core/clinical_graph_schema.py`.

### Graph edges

| Neo4j edge | From | To | Purpose |
|---|---|---|---|
| `HAS_EPISODE` | `Saga` | `Episodic` | Episode belongs to the patient's journey |
| `NEXT_EPISODE` | `Episodic` | `Episodic` | Chronological episode ordering |
| `MENTIONS` | `Episodic` | `Entity` | Episode contains or discusses the clinical entity |
| `RELATES_TO` | `Entity` | `Entity` | Temporal clinical fact extracted by Graphiti |
| `DERIVED_FROM` | `Episodic` | `FHIRSource` | Exact FHIR provenance for the episode |

`RELATES_TO` is the physical Neo4j relationship. Its clinical relationship
name is restricted to:

- `HAS_ENCOUNTER`
- `HAS_CONDITION`
- `HAS_MEDICATION_THERAPY`
- `HAS_CLINICAL_RESULT`
- `REPORTED_SYMPTOM`
- `UNDERWENT_PROCEDURE`
- `HAS_CARE_PLAN`
- `HAS_ALLERGY`
- `RECEIVED_IMMUNIZATION`
- `OCCURRED_DURING_ENCOUNTER`

`FHIRSource` stores only the resource identity, version, update time, and FHIR
store identity needed for citations. It does not duplicate the FHIR resource.
Semantic facts are returned only when their Graphiti episode resolves to a
patient-owned `FHIRSource` link.

The logical names carried by `RELATES_TO` are restricted to the clinical
schema in `clinical_core/clinical_graph_schema.py`. Exact lab values and calculated
trends are read from FHIR at query time rather than stored as semantic facts.

Patient requests read the existing semantic memory but never rebuild it in a
Cloud Run background thread. Rebuild explicitly with
`python scripts/rebuild_patient_memory.py --patient-id <id>` or the internal
memory rebuild endpoint so completion and provenance linking are observable.

Use a managed Neo4j deployment such as AuraDB. Do not run the database inside
Cloud Run. Configure the Cloud Run service with:

```text
NEO4J_URI=neo4j+s://<managed-host>
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<stored in Secret Manager>
NEO4J_DATABASE=neo4j
MEDGRAPHITI_ENABLED=true
```

## Repository data

DDInter CSV files, SNOMED CT release files, and patient exports are excluded
from the repository and must not be committed.
