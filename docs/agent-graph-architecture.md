# Agent and Patient Graph Architecture

## Agents and Tools

```mermaid
flowchart TB
    Q[Patient Question]
    C[Coordinator Agent]

    PS[Patient Search]
    FHIR[FHIR Search and Read]
    GRAPH[Graphiti Semantic Search]

    CLINICAL[Clinical Agent]
    PHARMACY[Pharmacy Agent]
    INSURANCE[Insurance Agent]

    CLINICAL_TOOLS[Clinical Evidence Tools<br/>PubMed]
    PHARMACY_TOOLS[Medication Tools<br/>RxNorm / DailyMed / DDInter]
    TERMINOLOGY[Terminology Tools<br/>RxNorm / ICD-10-CM / LOINC]

    Q --> C
    C --> PS

    PS --> FHIR
    PS --> GRAPH
    FHIR --> PS
    GRAPH --> PS
    PS --> C

    C -->|clinical interpretation| CLINICAL
    C -->|medication question| PHARMACY
    C -->|coverage question| INSURANCE

    CLINICAL --> PS
    CLINICAL --> CLINICAL_TOOLS

    PHARMACY --> FHIR
    PHARMACY --> PHARMACY_TOOLS

    INSURANCE --> FHIR
    C --> CLINICAL_TOOLS
    C --> PHARMACY_TOOLS
    C --> TERMINOLOGY
```

| Agent | Main responsibility | Tools used |
|---|---|---|
| Coordinator Agent | Understands the question, calls patient search, and selects one specialist when needed | Patient Search, FHIR, terminology, medication and evidence tools |
| Clinical Agent | Explains trends, concerns, follow-up and supporting research | Patient Search, FHIR, PubMed |
| Pharmacy Agent | Handles medication identity, labels, side effects and interactions | FHIR, RxNorm, DailyMed, DDInter |
| Insurance Agent | Explains coverage information recorded for the patient | FHIR |

## How Patient Search Runs

```mermaid
sequenceDiagram
    participant U as Patient
    participant C as Coordinator Agent
    participant S as Patient Search
    participant F as FHIR Record
    participant G as Graphiti Search
    participant T as Terminology Lookup

    U->>C: Natural-language question
    C->>S: Question + structured search plan
    S->>F: Retrieve exact patient records
    F-->>S: Conditions, visits, medications, labs and symptoms
    S->>G: Semantic search using the complete question
    G-->>S: Relevant patient facts + FHIR provenance
    S->>T: Validate codes present in selected evidence
    T-->>S: Canonical terminology labels
    S-->>C: Combined patient evidence + sources
    C-->>U: Grounded answer
```

Patient Search always combines:

1. Exact and structured patient-record retrieval.
2. Keyword and fuzzy matching over the patient record.
3. Graphiti semantic search over the patient memory graph.
4. FHIR provenance and terminology validation.

## Patient Memory Graph (Logical View)

```mermaid
flowchart LR
    PATIENT[Patient]
    EP1[Patient Episode]
    EP2[Patient Episode]
    VISIT[Visit]
    CONDITION[Condition]
    MEDICATION[Medication]
    OBSERVATION[Lab or Observation]
    SYMPTOM[Symptom]
    PROCEDURE[Procedure]
    CAREPLAN[Care Plan]
    ALLERGY[Allergy]
    IMMUNIZATION[Immunization]
    SOURCE[FHIR Source Record]

    PATIENT -->|HAS EPISODE| EP1
    PATIENT -->|HAS EPISODE| EP2
    EP1 -->|NEXT EPISODE| EP2

    EP1 -->|HAS VISIT| VISIT
    EP1 -->|RECORDS| CONDITION
    EP1 -->|RECORDS| MEDICATION
    EP1 -->|RECORDS| OBSERVATION
    EP1 -->|RECORDS| SYMPTOM
    EP1 -->|RECORDS| PROCEDURE
    EP1 -->|RECORDS| CAREPLAN
    EP1 -->|RECORDS| ALLERGY
    EP1 -->|RECORDS| IMMUNIZATION

    EP1 -->|DERIVED FROM| SOURCE

    PATIENT -->|HAS CONDITION| CONDITION
    PATIENT -->|HAS MEDICATION| MEDICATION
    PATIENT -->|HAS RESULT| OBSERVATION
    PATIENT -->|REPORTED SYMPTOM| SYMPTOM
    PATIENT -->|HAS VISIT| VISIT
    PATIENT -->|UNDERWENT| PROCEDURE
    PATIENT -->|HAS CARE PLAN| CAREPLAN
    PATIENT -->|HAS ALLERGY| ALLERGY
    PATIENT -->|RECEIVED| IMMUNIZATION

    CONDITION -->|RECORDED DURING| VISIT
    MEDICATION -->|RECORDED DURING| VISIT
    OBSERVATION -->|RECORDED DURING| VISIT
    SYMPTOM -->|RECORDED DURING| VISIT
```

### Nodes

| Node | Meaning |
|---|---|
| `Patient` | Patient represented inside an isolated graph partition |
| `PatientEpisode` | Visit or bounded group of clinical events belonging to the patient |
| `Visit` | Encounter during which clinical events were recorded |
| `Condition` | Recorded diagnosis, condition, or problem |
| `Medication` | Recorded medication therapy |
| `Observation` | Laboratory result, vital sign, or assessment |
| `Symptom` | Recorded symptom or complaint |
| `Procedure` | Diagnostic, therapeutic, or preventive procedure |
| `CarePlan` | Care plan, goal, or planned clinical activity |
| `Allergy` | Recorded allergy or intolerance |
| `Immunization` | Recorded vaccine administration |
| `FHIRSource` | Exact authoritative FHIR record supporting an episode |

### Edges

| Edge | Connection |
|---|---|
| `HAS_EPISODE` | Patient -> PatientEpisode |
| `NEXT_EPISODE` | PatientEpisode -> PatientEpisode |
| `HAS_VISIT` | Patient or PatientEpisode -> Visit |
| `RECORDS` | PatientEpisode -> clinical entity |
| `DERIVED_FROM` | PatientEpisode -> FHIRSource |
| `HAS_CONDITION` | Patient -> Condition |
| `HAS_MEDICATION` | Patient -> Medication |
| `HAS_RESULT` | Patient -> Observation |
| `REPORTED_SYMPTOM` | Patient -> Symptom |
| `UNDERWENT` | Patient -> Procedure |
| `HAS_CARE_PLAN` | Patient -> CarePlan |
| `HAS_ALLERGY` | Patient -> Allergy |
| `RECEIVED` | Patient -> Immunization |
| `RECORDED_DURING` | Clinical entity -> Visit |
