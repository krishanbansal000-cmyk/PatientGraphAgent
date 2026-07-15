# Avinia GCP Prototype

MyHealth is a patient-facing health assistant for synthetic-data prototyping.
It combines Google Cloud Healthcare API FHIR retrieval, an ADK agent, an
optional Neo4j/Graphiti patient-memory graph, and an Alpine.js interface.

Detailed UML, graph, data-contract, ingestion, retrieval, and deployment
diagrams are in [docs/architecture.md](docs/architecture.md).

## Local setup

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8080
```

Set the project, FHIR store, synthetic patient, prototype login, and optional
Neo4j values in `.env`. The file is intentionally excluded from Git. Local GCP
Application Default Credentials are required to query the configured FHIR
store and Vertex AI.

Open `http://localhost:8080/ui/`. The shared-password login is only for the
synthetic-data MVP and must be replaced before using real patient data.

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

For example, after creating a Secret Manager secret named
`myhealth-neo4j-password`, update the existing service:

```powershell
gcloud run services update myhealth-agent-api `
  --project avinia-app `
  --region us-central1 `
  --set-env-vars "NEO4J_URI=neo4j+s://<managed-host>,NEO4J_USERNAME=neo4j,NEO4J_DATABASE=neo4j,MEDGRAPHITI_ENABLED=true" `
  --set-secrets "NEO4J_PASSWORD=myhealth-neo4j-password:latest"
```

The Cloud Run service account needs `roles/secretmanager.secretAccessor` for
that secret. Graphiti constraints and indexes are created lazily during memory
ingestion. Exact patient retrieval continues to work from FHIR if semantic
memory is disabled or temporarily unavailable.

The retrieval unit tests use synthetic FHIR resources:

```powershell
python -m unittest discover -s tests -v
```

## Repository data

The DDInter CSV files used by the prototype interaction lookup are documented
in `data/ddinter/README.md`. Confirm their current redistribution terms before
making a public release. SNOMED CT release files and patient exports must not be
committed to this repository.
