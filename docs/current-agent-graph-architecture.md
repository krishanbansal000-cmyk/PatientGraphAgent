# Current Agent and Graph Architecture

This document gives a basic view of the current Avinia PatientGraphAgent POC.
It covers the agent flow, data boundaries, and Graphiti nodes and edges.

## Current Scope

- One patient is currently loaded into the clinical memory graph.
- Cloud Healthcare FHIR is the authoritative patient record.
- Graphiti and Neo4j store derived episodic and semantic memory.
- External terminology, drug, interaction, and literature datasets are accessed by agent tools.
- External datasets are not currently loaded as shared Neo4j knowledge nodes.

## Agent Architecture

```mermaid
flowchart TB
    UI[Alpine.js Patient UI]
    API[FastAPI /api/agent/ask]
    ROOT[ADK Root Agent<br/>Gemini 2.5 Flash]

    CONTEXT[search_patient_context<br/>Typed retrieval plan]
    FHIR[Cloud Healthcare FHIR<br/>Authoritative patient facts]
    MEMORY[Graphiti + Neo4j<br/>Derived semantic memory]

    DOCTOR[Doctor Agent]
    PHARMACY[Pharmacy Agent]
    INSURANCE[Insurance Agent]

    TERMINOLOGY[RxNorm / ICD-10-CM / LOINC]
    DRUGS[DailyMed / DDInter]
    LITERATURE[PubMed]

    UI --> API
    API --> ROOT

    ROOT --> CONTEXT
    CONTEXT --> FHIR
    CONTEXT --> MEMORY

    ROOT -. transfers to one specialist .-> DOCTOR
    ROOT -. transfers to one specialist .-> PHARMACY
    ROOT -. transfers to one specialist .-> INSURANCE

    DOCTOR --> FHIR
    DOCTOR --> LITERATURE
    PHARMACY --> FHIR
    PHARMACY --> TERMINOLOGY
    PHARMACY --> DRUGS
    INSURANCE --> FHIR

    ROOT --> TERMINOLOGY
    ROOT --> DRUGS
    ROOT --> LITERATURE
```

### Request Flow

1. The UI sends a question, patient ID, and session ID to FastAPI.
2. The root agent creates a typed patient retrieval plan.
3. `search_patient_context` retrieves and ranks canonical FHIR evidence.
4. Graphiti semantic search adds patient-scoped, provenance-linked memory facts.
5. The root agent may transfer to one specialist when focused interpretation is needed.
6. External medical knowledge is retrieved through tools when required.
7. FastAPI collects citations from actual tool responses and returns the answer to the UI.

The current implementation does not run the doctor, pharmacy, and insurance agents in parallel.

## Graphiti Graph

```mermaid
flowchart LR
    SAGA[Saga<br/>Patient journey]
    EP1[Episodic<br/>Clinical episode]
    EP2[Episodic<br/>Next episode]
    SOURCE[FHIRSource<br/>Resource identity and version]

    PATIENT[PatientRecordSubject]
    ENCOUNTER[ClinicalEncounter]
    CONDITION[ClinicalCondition]
    MEDICATION[MedicationTherapy]
    OBSERVATION[ClinicalObservation]
    SYMPTOM[PatientReportedSymptom]
    OTHER[Procedure / CarePlan<br/>Allergy / Immunization]

    SAGA -->|HAS_EPISODE| EP1
    SAGA -->|HAS_EPISODE| EP2
    EP1 -->|NEXT_EPISODE| EP2

    EP1 -->|MENTIONS| PATIENT
    EP1 -->|MENTIONS| ENCOUNTER
    EP1 -->|MENTIONS| CONDITION
    EP1 -->|MENTIONS| MEDICATION
    EP1 -->|MENTIONS| OBSERVATION
    EP1 -->|MENTIONS| SYMPTOM
    EP1 -->|MENTIONS| OTHER

    EP1 -->|DERIVED_FROM| SOURCE

    PATIENT -->|RELATES_TO: HAS_ENCOUNTER| ENCOUNTER
    PATIENT -->|RELATES_TO: HAS_CONDITION| CONDITION
    PATIENT -->|RELATES_TO: HAS_MEDICATION_THERAPY| MEDICATION
    PATIENT -->|RELATES_TO: HAS_CLINICAL_RESULT| OBSERVATION
    PATIENT -->|RELATES_TO: REPORTED_SYMPTOM| SYMPTOM

    CONDITION -->|RELATES_TO: OCCURRED_DURING_ENCOUNTER| ENCOUNTER
    MEDICATION -->|RELATES_TO: OCCURRED_DURING_ENCOUNTER| ENCOUNTER
    OBSERVATION -->|RELATES_TO: OCCURRED_DURING_ENCOUNTER| ENCOUNTER
    SYMPTOM -->|RELATES_TO: OCCURRED_DURING_ENCOUNTER| ENCOUNTER
```

## Node List

| Node | Purpose |
|---|---|
| `Saga` | Patient-scoped journey container |
| `Episodic` | Bounded dated clinical memory episode |
| `FHIRSource` | Pointer to the authoritative FHIR resource and version |
| `PatientRecordSubject` | Non-identifying patient entity within the graph partition |
| `ClinicalEncounter` | Visit, emergency encounter, or admission |
| `ClinicalCondition` | Recorded diagnosis, condition, or problem |
| `MedicationTherapy` | Medication order, statement, dispense, or administration |
| `ClinicalObservation` | Laboratory result, vital sign, or assessment |
| `PatientReportedSymptom` | Recorded symptom or complaint |
| `ClinicalProcedure` | Diagnostic, therapeutic, or preventive procedure |
| `ClinicalCarePlan` | Care plan, goal, or planned clinical activity |
| `ClinicalAllergy` | Recorded allergy or intolerance |
| `ClinicalImmunization` | Recorded vaccine administration |

Clinical nodes are Graphiti `Entity` nodes with the corresponding typed label.

## Edge List

| Edge | Meaning |
|---|---|
| `HAS_EPISODE` | Connects a patient journey Saga to its episodes |
| `NEXT_EPISODE` | Orders episodes chronologically |
| `MENTIONS` | Connects an episode to entities present in that episode |
| `DERIVED_FROM` | Connects an episode to its canonical FHIR provenance |
| `RELATES_TO` | Physical Graphiti relationship between typed clinical entities |

The allowed clinical names carried by `RELATES_TO` are:

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

## Data Boundaries

```text
FHIR
  Authoritative patient conditions, medications, observations, dates and statuses

Graphiti / Neo4j
  Patient episodes, semantic entities, temporal facts and FHIR provenance pointers

Agent tools
  RxNorm, ICD-10-CM, LOINC, DailyMed, DDInter and PubMed
```

Graphiti facts are treated as derived context. Exact clinical claims must remain supported by the connected FHIR evidence.
