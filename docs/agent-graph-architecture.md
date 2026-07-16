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

## Patient Memory Graph

```mermaid
flowchart LR
    JOURNEY[Patient Journey]
    EP1[Patient Episode]
    EP2[Patient Episode]
    SOURCE[FHIR Source Record]

    PATIENT[Patient]
    VISIT[Visit]
    CONDITION[Condition]
    MEDICATION[Medication]
    OBSERVATION[Lab or Observation]
    SYMPTOM[Symptom]
    OTHER[Procedure, Care Plan,<br/>Allergy or Immunization]

    JOURNEY -->|HAS EPISODE| EP1
    JOURNEY -->|HAS EPISODE| EP2
    EP1 -->|NEXT EPISODE| EP2

    EP1 -->|MENTIONS| PATIENT
    EP1 -->|MENTIONS| VISIT
    EP1 -->|MENTIONS| CONDITION
    EP1 -->|MENTIONS| MEDICATION
    EP1 -->|MENTIONS| OBSERVATION
    EP1 -->|MENTIONS| SYMPTOM
    EP1 -->|MENTIONS| OTHER

    EP1 -->|DERIVED FROM| SOURCE

    PATIENT -->|HAS CONDITION| CONDITION
    PATIENT -->|HAS MEDICATION| MEDICATION
    PATIENT -->|HAS RESULT| OBSERVATION
    PATIENT -->|REPORTED SYMPTOM| SYMPTOM
    PATIENT -->|HAS VISIT| VISIT

    CONDITION -->|RECORDED DURING| VISIT
    MEDICATION -->|RECORDED DURING| VISIT
    OBSERVATION -->|RECORDED DURING| VISIT
    SYMPTOM -->|RECORDED DURING| VISIT
```

### Graph Terms

| Term | Meaning |
|---|---|
| Patient | The patient represented inside an isolated graph partition |
| Patient Journey | The timeline container that keeps the patient's episodes ordered |
| Patient Episode | One visit or bounded group of clinical events at a point in time |
| Clinical entities | Conditions, medications, observations, symptoms, visits and other patient facts mentioned in an episode |
| FHIR Source Record | The exact authoritative record that supports an episode and its extracted facts |

## Data Boundary

```mermaid
flowchart LR
    PATIENT_DATA[FHIR Patient Record]
    MEMORY[Graphiti Patient Memory]
    TOOLS[Medical Dataset Tools]
    AGENT[Agents]

    PATIENT_DATA -->|exact patient facts| AGENT
    MEMORY -->|semantic patient context| AGENT
    TOOLS -->|terminology, drug and literature evidence| AGENT
```

The graph contains the patient journey and patient-specific semantic memory. RxNorm, ICD-10-CM, LOINC, DailyMed, DDInter and PubMed remain connected through tools rather than being copied into the patient graph.
