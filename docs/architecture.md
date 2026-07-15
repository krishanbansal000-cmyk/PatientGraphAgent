# MyHealth Agent and Clinical Memory Architecture

Status: current deployed POC architecture, July 2026

This document describes the MyHealth patient-facing agent, its authoritative
FHIR data path, deterministic clinical retrieval, Graphiti semantic memory,
provenance model, specialist agents, data contracts, and deployment topology.

## 1. Architectural principles

1. Google Cloud Healthcare FHIR is the clinical source of truth.
2. Exact values, dates, statuses, codes, and resource identity come from FHIR.
3. Graphiti is derived semantic memory, not a replacement patient database.
4. Every Graphiti fact returned to the agent must resolve to patient-owned FHIR
   provenance.
5. Exact operations such as complete lab series and current medication lists
   use deterministic FHIR processing before the LLM writes an explanation.
6. The LLM interprets questions and explains evidence; it must not select an
   incomplete subset when a deterministic operation is available.
7. Patient graph data is isolated by a hashed `group_id`.
8. Graph ingestion is explicit and synchronous. Normal patient reads never
   rebuild memory in the background.

## 2. System context

```mermaid
flowchart LR
    Patient[Patient or internal tester]

    subgraph GCP[Google Cloud project: avinia-app]
        UI[Alpine.js single-page UI]
        API[FastAPI service on Cloud Run]
        ADK[Google ADK root and specialist agents]
        Vertex[Vertex AI Gemini 2.5 Flash]
        FHIR[Cloud Healthcare FHIR store]
        BigQuery[BigQuery terminology tables]
    end

    subgraph Knowledge[External and packaged knowledge]
        RxNav[RxNorm REST]
        LOINC[NLM Clinical Tables LOINC]
        DailyMed[DailyMed SPL labels]
        DDInter[DDInter 2.0 packaged CSV data]
    end

    subgraph Memory[Managed semantic memory]
        Aura[Neo4j Aura with Graphiti]
    end

    Patient -->|HTTPS and prototype cookie| UI
    UI -->|REST JSON| API
    API --> ADK
    ADK --> Vertex
    API --> FHIR
    ADK --> FHIR
    ADK --> BigQuery
    ADK --> RxNav
    ADK --> LOINC
    ADK --> DailyMed
    ADK --> DDInter
    ADK --> Aura
    Aura -.->|FHIRSource identity only| FHIR
```

## 3. Runtime component architecture

```mermaid
flowchart TB
    subgraph Browser
        Alpine[Alpine.js state]
        Dashboard[Patient dashboard]
        JourneyUI[Journey timeline and detail drawer]
        Chat[AI chat and source list]
        Alpine --> Dashboard
        Alpine --> JourneyUI
        Alpine --> Chat
    end

    subgraph CloudRun[Cloud Run: myhealth-agent-api]
        Auth[Prototype cookie authentication]
        Routes[FastAPI routes]
        Session[In-memory ADK session service]
        Citations[Citation sanitizer and collector]

        Root[myhealth_assistant]
        Doctor[doctor_agent]
        Pharmacy[pharmacy_agent]
        Insurance[insurance_agent]

        ContextTool[search_patient_context]
        FHIRTools[search_fhir and read_fhir]
        MedicationTool[resolve_medication]
        LabTool[resolve_lab_code]
        InteractionTool[check_interaction]
        LabelTool[get_drug_info]

        Journey[Patient journey builder]
        MemoryService[PatientMemoryService]
    end

    Browser --> Auth --> Routes
    Routes --> Session --> Root
    Root --> ContextTool
    Root --> FHIRTools
    Root --> MedicationTool
    Root --> LabTool
    Root --> InteractionTool
    Root --> LabelTool
    Root -. transfer when needed .-> Doctor
    Root -. transfer when needed .-> Pharmacy
    Root -. transfer when needed .-> Insurance
    ContextTool --> MemoryService
    Routes --> Journey
    Routes --> Citations
    Root --> Citations
```

### Agent responsibilities

| Agent | Responsibility | Tools |
|---|---|---|
| `myhealth_assistant` | Primary conversation, retrieval routing, patient-friendly answer | All patient and knowledge tools |
| `doctor_agent` | Trends, clinical interpretation, red flags, clinician follow-up | Context, FHIR search/read |
| `pharmacy_agent` | Medication identity, label information, interactions | FHIR, RxNorm, DailyMed, DDInter |
| `insurance_agent` | Recorded coverage and benefit uncertainty | FHIR search/read |

Specialists are selected by the root agent. They are not run in parallel. Every
patient-record question must first call `search_patient_context` exactly once.

## 4. Hybrid patient question flow

```mermaid
flowchart TD
    Q[Patient question]
    GeminiPlan[Gemini emits PatientQueryPlan tool argument]
    Validate[Pydantic validation and safe FHIR category mapping]
    Load[Load Patient FHIR compartment]
    Project[Project FHIR resources to ClinicalEvent objects]

    Structured[Structured category and status search]
    Text[Supporting keyword and fuzzy recall]
    Fuse[Fuse and rank results]
    Expand[Add encounter, reference, and nearby context]

    Exact{Operation in validated plan}
    LabSeries[Complete lab series by LOINC code]
    CurrentMeds[Complete current medication set]
    Semantic[Patient-scoped Graphiti semantic search]
    Terminology[Validate source codes with terminology data]
    Evidence[Build bounded evidence and source list]
    Explain[Gemini explains retrieved evidence]
    Response[Answer plus structured citations]

    Q --> GeminiPlan --> Validate --> Load --> Project
    Project --> Structured
    Project --> Text
    Structured --> Fuse
    Text --> Fuse
    Fuse --> Expand --> Exact
    Exact -->|lab series| LabSeries
    Exact -->|current medications| CurrentMeds
    Exact -->|search or timeline| Semantic
    LabSeries --> Semantic
    CurrentMeds --> Semantic
    Semantic --> Terminology --> Evidence --> Explain --> Response
```

### What is deterministic and what is generative

| Concern | Deterministic | Generative |
|---|---:|---:|
| Patient identity and partition | Yes | No |
| FHIR resource loading | Yes | No |
| Natural-language date interpretation | No | Yes |
| ISO date validation and filtering | Yes | No |
| Complete lab series | Yes | No |
| Current medication status filtering | Yes | No |
| Code and exact value preservation | Yes | No |
| Citation resource identity | Yes | No |
| Vague intent and concept interpretation | No | Yes |
| Semantic relationship retrieval | Search is bounded | Graph facts were extracted by Graphiti |
| Clinical explanation wording | No | Yes |
| Diagnosis or causality inference | Prohibited | Prohibited |

## 5. Exact HbA1c sequence

```mermaid
sequenceDiagram
    actor User
    participant UI as Alpine UI
    participant API as FastAPI
    participant Root as ADK root agent
    participant Context as search_patient_context
    participant FHIR as Healthcare FHIR
    participant Graph as Graphiti/Neo4j
    participant LLM as Gemini

    User->>UI: How did my HbA1c change over time?
    UI->>API: POST /api/agent/ask
    API->>Root: question plus patient session state
    Root->>Context: question + typed plan<br/>intent=result, concept=HbA1c,<br/>operation=lab_series
    Context->>FHIR: Patient/{id}/$everything
    FHIR-->>Context: FHIR Bundle
    Context->>Context: validate plan and derive Observation category
    Context->>Context: find matching recorded LOINC code
    Context->>Context: filter every matching Observation
    Context->>Context: sort by effectiveDateTime
    Context->>Graph: semantic search in patient group
    Graph-->>Context: provenance-grounded optional facts
    Context-->>Root: deterministic_result plus five FHIR sources
    Root->>LLM: explain complete ordered evidence
    LLM-->>Root: patient-friendly text
    Root-->>API: answer and tool events
    API->>API: collect and sanitize exact sources
    API-->>UI: answer, grounded=true, five citations
    UI-->>User: answer and source list
```

The LLM does not decide which HbA1c observations to include. It receives the
complete ordered series and controls only the final explanation.

## 6. Authoritative FHIR data model

```mermaid
classDiagram
    class Patient {
        +string id
        +string gender
        +date birthDate
    }
    class Encounter {
        +string id
        +string status
        +string class
        +datetime periodStart
        +datetime periodEnd
    }
    class Condition {
        +string id
        +CodeableConcept code
        +string clinicalStatus
        +datetime onset
        +datetime recordedDate
    }
    class MedicationRequest {
        +string id
        +CodeableConcept medication
        +string status
        +string intent
        +datetime authoredOn
    }
    class Observation {
        +string id
        +CodeableConcept code
        +string status
        +decimal value
        +string unit
        +datetime effectiveDateTime
    }
    class AllergyIntolerance {
        +string id
        +CodeableConcept code
        +string clinicalStatus
        +datetime onset
    }
    class Procedure {
        +string id
        +CodeableConcept code
        +string status
        +datetime performed
    }
    class CarePlan {
        +string id
        +string status
        +string intent
        +datetime periodStart
    }

    Patient "1" --> "0..*" Encounter : subject
    Patient "1" --> "0..*" Condition : subject
    Patient "1" --> "0..*" MedicationRequest : subject
    Patient "1" --> "0..*" Observation : subject
    Patient "1" --> "0..*" AllergyIntolerance : patient
    Patient "1" --> "0..*" Procedure : subject
    Patient "1" --> "0..*" CarePlan : subject
    Encounter "0..1" <-- "0..*" Condition : encounter
    Encounter "0..1" <-- "0..*" MedicationRequest : encounter
    Encounter "0..1" <-- "0..*" Observation : encounter
    Encounter "0..1" <-- "0..*" Procedure : encounter
```

FHIR resources are never replaced by simplified database rows. Search and
journey objects are request-time projections that retain the original resource
type and ID for re-reading and citations.

## 7. Retrieval data structures

```mermaid
classDiagram
    class PatientQueryPlan {
        +string[] intents
        +string[] concepts
        +string time_scope
        +date date_start
        +date date_end
        +string output_mode
        +string scope
        +boolean references_prior_context
        +string operation
        +string target_code
    }

    class PatientSearchPlan {
        +string intent
        +string[] intents
        +string[] query_terms
        +string[] expanded_terms
        +string[] resource_types
        +string time_scope
        +date date_start
        +date date_end
        +string output_mode
        +string scope
        +boolean references_prior_context
        +string operation
        +string target_code
    }

    class ClinicalEvent {
        +string resource_type
        +string resource_id
        +string display
        +string summary
        +datetime event_time
        +string event_time_kind
        +datetime recorded_time
        +datetime last_updated
        +string encounter_id
        +string status
        +string[] codes
        +string[] references
    }

    class RankedClinicalEvent {
        +ClinicalEvent event
        +float score
        +string[] reasons
    }

    class DeterministicResult {
        +string kind
        +string label
        +string code
        +boolean complete
        +ClinicalEvent[] events
    }

    class PatientSnapshot {
        +ClinicalEvent[] active_conditions
        +ClinicalEvent[] current_medications
        +ClinicalEvent[] allergies
        +ClinicalEvent[] recent_results
        +ClinicalEvent[] recent_visits
    }

    class PatientContextResult {
        +string query
        +string resolution_status
        +string clarification_question
        +RankedClinicalEvent[] relevant_events
        +ClinicalEvent[] related_context
        +ClinicalEvent[] essential_context
        +ClinicalEvent[] timeline_events
        +string[] retrieval_modes
    }

    PatientQueryPlan --> PatientSearchPlan : validated and materialized
    PatientContextResult *-- PatientSearchPlan
    PatientContextResult *-- PatientSnapshot
    PatientContextResult *-- DeterministicResult
    PatientContextResult *-- RankedClinicalEvent
    RankedClinicalEvent *-- ClinicalEvent
    DeterministicResult *-- ClinicalEvent
```

### Deterministic result example

```json
{
  "kind": "lab_series",
  "label": "HbA1c",
  "code": "4548-4",
  "complete": true,
  "events": [
    {
      "resource_type": "Observation",
      "resource_id": "obs-longitudinal-4548-4-1",
      "event_time": "2018-01-15T06:00:00+00:00",
      "summary": "HbA1c; status final; value 7.8 %"
    }
  ]
}
```

## 8. Patient journey and episode structures

```mermaid
classDiagram
    class PatientJourney {
        +string patient_id
        +string group_id
        +datetime generated_at
        +string source
        +JourneyCurrentState current_state
        +JourneyEpisode[] episodes
        +int total_resources
        +int dated_resources
        +int undated_resources
    }

    class JourneyEpisode {
        +string id
        +string type
        +datetime date
        +datetime end_date
        +string title
        +string status
        +string encounter_id
        +string summary
        +JourneyItem[] items
        +JourneyChange[] changes
        +JourneyCitation[] citations
        +map category_counts
    }

    class JourneyItem {
        +string resource_type
        +string resource_id
        +string reference
        +string category
        +string display
        +string summary
        +string status
        +datetime date
        +string code
        +string value
        +string unit
        +string interpretation
    }

    class JourneyChange {
        +string kind
        +string category
        +string label
        +string resource_reference
    }

    class JourneyCitation {
        +string reference
        +string resource_type
        +string resource_id
        +string version
        +datetime last_updated
    }

    class JourneyCurrentState {
        +JourneyItem[] active_conditions
        +JourneyItem[] current_medications
        +JourneyItem[] allergies
        +JourneyItem[] recent_results
        +JourneyItem[] recent_visits
    }

    PatientJourney *-- JourneyCurrentState
    PatientJourney *-- JourneyEpisode
    JourneyEpisode *-- JourneyItem
    JourneyEpisode *-- JourneyChange
    JourneyEpisode *-- JourneyCitation
```

### Episode formation rules

1. Every Encounter becomes an encounter episode.
2. Resources referencing that Encounter are grouped into the same episode.
3. Remaining resources are grouped by clinical category and clinical date.
4. Undated resources remain visible in the UI journey but are excluded from
   temporal Graphiti ingestion by default.
5. Episodes are sorted using clinical time, not FHIR `meta.lastUpdated`.
6. Dense episodes are split into memory parts of at most six clinical items.
7. A visit anchor is retained in every split part when present.
8. Episode changes are derived by comparing repeated coded clinical items.
9. No causal relationship is inferred merely because events are close in time.

## 9. Graphiti physical graph

```mermaid
erDiagram
    SAGA ||--o{ EPISODIC : HAS_EPISODE
    EPISODIC ||--o{ EPISODIC : NEXT_EPISODE
    EPISODIC ||--o{ ENTITY : MENTIONS
    ENTITY }o--o{ ENTITY : RELATES_TO
    EPISODIC ||--o{ FHIR_SOURCE : DERIVED_FROM

    SAGA {
        string uuid
        string name
        string group_id
        string first_episode_uuid
        string last_episode_uuid
    }
    EPISODIC {
        string uuid
        string name
        string group_id
        datetime valid_at
        datetime created_at
        boolean avinia_active
        string patient_id
        string saga
    }
    ENTITY {
        string uuid
        string name
        string group_id
        string summary
        string[] labels
        vector name_embedding
    }
    FHIR_SOURCE {
        string graph_key
        string fhir_key
        string resource_type
        string resource_id
        string source_id
        string fhir_version
        datetime last_updated
    }
```

`AviniaMemoryEpisodeMap` is an application-owned idempotency and lease record.
It is not part of the clinical graph presented to the agent.

```text
AviniaMemoryEpisodeMap
  group_id
  logical_id
  content_hash
  graphiti_uuid
  status: pending | complete | failed
  owner_token
  lease_expires_at
  created_at
  completed_at
```

### Graph node roles

| Node | Role | Authoritative? |
|---|---|---:|
| `Saga` | Orders one patient's clinical journey episodes | No |
| `Episodic` | Temporal memory created from a bounded journey episode | No |
| `Entity` plus clinical label | Semantic representation extracted by Graphiti | No |
| `FHIRSource` | Compact pointer to the exact canonical FHIR resource | Identity only |
| `AviniaMemoryEpisodeMap` | Idempotent ingestion bookkeeping | Operational only |

### Graph edge roles

| Edge | From | To | Meaning |
|---|---|---|---|
| `HAS_EPISODE` | Saga | Episodic | Episode belongs to the journey |
| `NEXT_EPISODE` | Episodic | Episodic | Chronological memory order |
| `MENTIONS` | Episodic | Entity | Episode contains the entity |
| `RELATES_TO` | Entity | Entity | Graphiti semantic fact with temporal metadata |
| `DERIVED_FROM` | Episodic | FHIRSource | Exact source resources used to create episode |

## 10. Logical clinical semantic schema

Graphiti stores physical semantic edges as `RELATES_TO`. The typed extraction
schema constrains the logical relationship name and permitted endpoint types.

```mermaid
classDiagram
    class PatientRecordSubject
    class ClinicalEncounter
    class ClinicalCondition
    class MedicationTherapy
    class ClinicalObservation
    class PatientReportedSymptom
    class ClinicalProcedure
    class ClinicalCarePlan
    class ClinicalAllergy
    class ClinicalImmunization

    PatientRecordSubject --> ClinicalEncounter : HAS_ENCOUNTER
    PatientRecordSubject --> ClinicalCondition : HAS_CONDITION
    PatientRecordSubject --> MedicationTherapy : HAS_MEDICATION_THERAPY
    PatientRecordSubject --> ClinicalObservation : HAS_CLINICAL_RESULT
    PatientRecordSubject --> PatientReportedSymptom : REPORTED_SYMPTOM
    PatientRecordSubject --> ClinicalProcedure : UNDERWENT_PROCEDURE
    PatientRecordSubject --> ClinicalCarePlan : HAS_CARE_PLAN
    PatientRecordSubject --> ClinicalAllergy : HAS_ALLERGY
    PatientRecordSubject --> ClinicalImmunization : RECEIVED_IMMUNIZATION

    ClinicalCondition --> ClinicalEncounter : OCCURRED_DURING_ENCOUNTER
    MedicationTherapy --> ClinicalEncounter : OCCURRED_DURING_ENCOUNTER
    ClinicalObservation --> ClinicalEncounter : OCCURRED_DURING_ENCOUNTER
    PatientReportedSymptom --> ClinicalEncounter : OCCURRED_DURING_ENCOUNTER
    ClinicalProcedure --> ClinicalEncounter : OCCURRED_DURING_ENCOUNTER
    ClinicalImmunization --> ClinicalEncounter : OCCURRED_DURING_ENCOUNTER
```

This is a closed extraction vocabulary. The ingestion sanitizer removes any
relationship with another name or an invalid source/target signature. It also
prevents Graphiti from storing calculated lab changes. Treatment indications,
drug interactions, recommendations, causal findings, and cohort membership are
resolved by deterministic sources or a separately reviewed reasoning layer;
temporal proximity cannot create them.

## 11. Graph ingestion sequence

```mermaid
sequenceDiagram
    actor Operator
    participant API as Memory rebuild endpoint or script
    participant FHIR as Healthcare FHIR
    participant Journey as Journey builder
    participant Convert as Episode converter
    participant Map as AviniaMemoryEpisodeMap
    participant Graphiti
    participant Prov as FHIR provenance bridge
    participant Neo4j

    Operator->>API: explicit rebuild(patient_id)
    API->>FHIR: load Patient/$everything
    FHIR-->>API: canonical resources
    API->>Journey: build_patient_journey
    Journey-->>API: dated encounter and standalone episodes
    API->>Convert: build_memory_episodes
    Convert-->>API: stable logical IDs and content hashes

    loop Oldest to newest episode
        API->>Map: reserve group_id + logical_id + content_hash
        alt Existing complete mapping
            Map-->>API: reuse graphiti_uuid
        else New or reclaimable mapping
            API->>Graphiti: add_episode with typed schema and saga
            Graphiti->>Neo4j: Saga, Episodic, Entity, semantic edges
            Graphiti-->>API: episode UUID
            API->>Map: mark mapping complete
        end
    end

    API->>Prov: link episode UUIDs to exact FHIR references
    Prov->>Neo4j: MERGE FHIRSource and provenance edges
    API->>Prov: mark current episode UUIDs active
    Prov->>Neo4j: set avinia_active snapshot
    API-->>Operator: ingestion counts and fallback status
```

### Idempotency

The unique key is:

```text
(group_id, logical_id, content_hash)
```

An unchanged episode is reused. Changed FHIR content produces a new content hash
and therefore a new memory revision. Old revisions remain stored but are marked
inactive by `avinia_active=false`.

## 12. Semantic retrieval and provenance gate

```mermaid
flowchart LR
    Question[Question]
    Hash[Derive patient group_id]
    Search[Graphiti hybrid search]
    Candidate[Candidate RELATES_TO facts]
    Episodes[Resolve supporting episode UUIDs]
    Gate{Active patient-owned provenance exists?}
    Sources[Return fact plus FHIRSource records]
    Reject[Exclude ungrounded fact]
    Fallback[Continue with canonical FHIR context]

    Question --> Hash --> Search --> Candidate --> Episodes --> Gate
    Gate -->|yes| Sources
    Gate -->|no| Reject
    Search -. Vertex or graph unavailable .-> Fallback
```

For a fact to reach the agent:

1. The Graphiti search is restricted to the patient's `group_id`.
2. The fact must reference one or more Graphiti episode UUIDs.
3. The episode must match the same patient ID and group ID.
4. The episode must have `avinia_active=true`.
5. The episode must have a patient-owned `DERIVED_FROM` FHIRSource edge.
6. The returned source identifies the exact FHIR resource and version.

Facts failing the gate are counted as excluded and are not returned.

## 13. Multi-patient isolation

```mermaid
flowchart TB
    P1[Patient A FHIR ID] --> H1[SHA-256 prefix]
    P2[Patient B FHIR ID] --> H2[SHA-256 prefix]
    H1 --> G1[group_id patient_aaa]
    H2 --> G2[group_id patient_bbb]

    G1 --> S1[Saga A]
    G1 --> E1[Episodes A]
    G1 --> N1[Entities A]
    G1 --> F1[FHIRSource A]

    G2 --> S2[Saga B]
    G2 --> E2[Episodes B]
    G2 --> N2[Entities B]
    G2 --> F2[FHIRSource B]

    E1 -. no traversal .- E2
```

The raw patient ID is not used as Graphiti's group name. The stable group is:

```text
patient_ + first 24 hex characters of SHA-256(patient_id)
```

API chat sessions are also bound to one patient. Reusing the same session with
a different patient ID returns HTTP 409.

## 14. Terminology and knowledge sources

```mermaid
flowchart LR
    FHIRCode[FHIR coding already present]
    RxBQ[Versioned BigQuery RxNorm]
    ICDBQ[Project ICD-10-CM table]
    RxAPI[RxNorm REST]
    LoincAPI[NLM LOINC Clinical Tables]
    SPL[DailyMed SPL]
    DDI[DDInter 2.0]

    FHIRCode -->|exact RxCUI validation| RxBQ
    FHIRCode -->|exact ICD code validation| ICDBQ
    RxAPI -->|vague or misspelled medication| ResolvedDrug[Resolved medication]
    LoincAPI -->|lab name or exact code| ResolvedLab[Resolved LOINC concept]
    ResolvedDrug --> SPL
    ResolvedDrug --> DDI
```

| Source | Purpose | May create patient facts? |
|---|---|---:|
| FHIR coding | Recorded patient terminology | Yes, because it is in FHIR |
| BigQuery RxNorm | Validate an existing RxCUI | No |
| BigQuery ICD-10-CM | Validate an existing ICD code | No |
| RxNorm REST | Normalize a user-supplied drug name | No |
| NLM LOINC | Resolve a lab name or code | No |
| DailyMed | Official label information | No |
| DDInter | Drug interaction lookup | No |

Terminology sources enrich and explain recorded facts. They must not create a
diagnosis that is absent from the patient's FHIR record.

## 15. API contracts

### Ask request

```json
{
  "question": "How did my HbA1c change over time?",
  "patient_id": "FHIR patient ID",
  "session_id": "conversation session",
  "user_id": "prototype user",
  "episode_resource_ids": ["Observation/example"]
}
```

### Ask response

```json
{
  "question": "How did my HbA1c change over time?",
  "patient_id": "FHIR patient ID",
  "session_id": "conversation session",
  "answer": "Patient-friendly explanation",
  "grounded": true,
  "citations": [
    {
      "number": 1,
      "id": "fhir:Observation/example",
      "type": "patient_record",
      "title": "HbA1c",
      "publisher": "Connected FHIR record",
      "resource_type": "Observation",
      "resource_id": "example",
      "date": "2018-01-15T06:00:00+00:00",
      "tools": ["search_patient_context"]
    }
  ]
}
```

### Main endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/login` | Prototype shared-password login |
| `POST /api/auth/logout` | Remove prototype session cookie |
| `GET /api/auth/status` | Check prototype authentication |
| `GET /api/patient/{id}/details` | Structured FHIR patient overview |
| `GET /api/patient/{id}/journey` | Deterministic patient journey |
| `GET /api/fhir/{type}/{id}` | Read exact FHIR resource |
| `GET /api/fhir/{type}/{id}/related` | Resolve related FHIR references |
| `POST /api/agent/ask` | Run patient-scoped agent conversation |
| `GET /api/internal/patient/{id}/memory/status` | Process-local memory status |
| `POST /api/internal/patient/{id}/memory/rebuild` | Explicit synchronous memory rebuild |

## 16. Citation architecture

```mermaid
flowchart LR
    Tool[Successful tool call]
    Metadata[Structured sources array]
    Collector[collect_event_citations]
    Allowlist[Type, field, and URL allowlists]
    Dedupe[Deduplicate by source ID]
    Number[Assign display numbers]
    UI[Sources below answer]

    Tool --> Metadata --> Collector --> Allowlist --> Dedupe --> Number --> UI
```

Allowed source classes are:

- Patient FHIR record
- DailyMed drug label
- RxNorm, LOINC, or ICD terminology
- DDInter interaction lookup

Model-written source claims are ignored. A citation must originate in a tool
response. Exact deterministic operations narrow the source list to their exact
FHIR evidence keys before citation collection.

## 17. Deployment topology

```mermaid
flowchart TB
    Developer[Local repository]
    Build[Google Cloud Build]
    Registry[Artifact Registry]
    Revision[Cloud Run revision]
    Traffic[Cloud Run traffic routing]
    Service[myhealth-agent-api]

    Developer -->|gcloud builds submit| Build
    Build -->|Docker image| Registry
    Registry -->|deploy with no traffic| Revision
    Revision -->|tagged verification URL| Verify[Smoke and answer tests]
    Verify -->|pass| Traffic
    Traffic -->|100 percent| Service
```

Runtime dependencies:

```mermaid
flowchart LR
    CloudRun[Cloud Run revision]
    Vertex[Vertex AI]
    Healthcare[Healthcare API]
    BigQuery[BigQuery]
    Aura[Neo4j Aura]
    PublicAPIs[NLM public APIs]

    CloudRun --> Vertex
    CloudRun --> Healthcare
    CloudRun --> BigQuery
    CloudRun --> Aura
    CloudRun --> PublicAPIs
```

Graphiti Vertex calls use retry with exponential backoff and jitter for 429 and
5xx responses. If semantic retrieval remains unavailable, the patient answer
continues using canonical FHIR context.

## 18. Failure and fallback behavior

| Failure | Behavior | Clinical effect |
|---|---|---|
| Graphiti disabled | Return no semantic facts | Exact FHIR retrieval continues |
| Graphiti or Vertex failure | Record fallback reason | Exact FHIR retrieval continues |
| Ungrounded Graphiti fact | Exclude fact | It cannot influence the answer |
| RxNorm or ICD enrichment failure | Retain original FHIR coding with warning | Patient fact remains unchanged |
| DailyMed unavailable | Return no label result | Agent must not invent label content |
| DDInter no matching pair | Return no recorded interaction match | Must not claim proven absence of all interactions |
| FHIR unavailable | Patient-record query fails | No model-only patient answer |
| Ambiguous medication | Ask which recorded medicine | No guessed medication identity |
| Unknown patient term | Return `not_found` | No unrelated recent fact is presented as a match |

## 19. Example graph queries

### Entire active patient memory graph

```cypher
MATCH path =
  (saga:Saga)-[:HAS_EPISODE]->
  (episode:Episodic)-[:MENTIONS]->
  (entity:Entity)
WHERE episode.group_id = $group_id
  AND episode.avinia_active = true
RETURN path
LIMIT 500;
```

### Episodes with exact FHIR provenance

```cypher
MATCH path =
  (episode:Episodic)-[:DERIVED_FROM]->
  (source:FHIRSource)
WHERE episode.group_id = $group_id
  AND episode.avinia_active = true
RETURN path
LIMIT 500;
```

### Entity facts and their source episodes

```cypher
MATCH (left:Entity)-[fact:RELATES_TO]->(right:Entity)
WHERE fact.group_id = $group_id
OPTIONAL MATCH (episode:Episodic)-[:MENTIONS]->(left)
WHERE episode.group_id = $group_id
  AND episode.avinia_active = true
OPTIONAL MATCH (episode)-[:DERIVED_FROM]->(source:FHIRSource)
RETURN left, fact, right, episode, source
LIMIT 300;
```

### Chronological episode chain

```cypher
MATCH path =
  (first:Episodic)-[:NEXT_EPISODE*0..]->(later:Episodic)
WHERE first.group_id = $group_id
  AND first.avinia_active = true
  AND later.avinia_active = true
RETURN path
LIMIT 200;
```

## 20. Current boundaries and extension points

### Implemented now

- Canonical FHIR patient retrieval
- Gemini-generated, Pydantic-validated patient query plans
- Structured FHIR retrieval with supporting keyword and fuzzy recall
- Deterministic current medication results
- Deterministic complete lab series
- Encounter-aware patient journey
- Graphiti episodic semantic memory
- Patient-scoped provenance gate
- RxNorm, LOINC, ICD-10-CM, DailyMed, and DDInter access
- Root plus doctor, pharmacy, and insurance agents
- Structured source metadata in the UI

### Schema-ready but not yet a complete service

- Guideline ingestion and versioning
- Evidence-backed recommendation lifecycle
- Physician review and feedback nodes
- Population cohort computation
- Recommendation acceptance or rejection workflow
- Deterministic before/after medication event operator
- Deterministic visit-summary operator
- Full claim-to-citation mapping for every broad narrative sentence

These additions should preserve the same boundary: FHIR and reviewed evidence
remain authoritative, while Graphiti stores temporal semantic memory and
explainable relationships.

## 21. Non-negotiable invariants

1. Never overwrite canonical FHIR with Graphiti output.
2. Never return a Graphiti fact without patient-owned active provenance.
3. Never infer diagnosis or causality from temporal proximity alone.
4. Never invent terminology mappings or citations.
5. Never use one patient's group ID to search another patient's graph.
6. Never use FHIR `meta.lastUpdated` as clinical onset when a clinical date is
   available.
7. Never let a generic top-N search truncate an exact complete operation.
8. Never rebuild Graphiti memory as an implicit side effect of a patient read.
9. Never return external medical knowledge as though it were recorded patient
   history.
10. Every answer must remain useful when Graphiti is unavailable by falling
    back to canonical FHIR evidence.
