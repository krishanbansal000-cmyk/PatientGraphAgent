"""
MyHealth ADK tools backed by Google Cloud Healthcare API FHIR store.
Public knowledge lookups (FDA/RxNorm) still use BigQuery public datasets.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext
from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession

from agent.context_search import (
    PATIENT_RESOURCE_TYPES,
    PatientQueryPlan,
    build_patient_context,
    build_search_plan,
)
from agent.patient_memory import get_patient_memory_service
from agent.terminology_enrichment import (
    get_terminology_enricher,
    terminology_sources,
)
from agent_v2.sources import deduplicate_sources, fhir_source


log = logging.getLogger(__name__)

# FHIR store configuration
_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
_DATASET_ID = os.environ.get("HEALTHCARE_DATASET", "myhealth-dataset")
_FHIR_STORE_ID = os.environ.get("HEALTHCARE_FHIR_STORE", "myhealth-fhir-store")
_FHIR_BASE_URL = (
    f"https://healthcare.googleapis.com/v1/projects/{_PROJECT_ID}/locations/{_LOCATION}"
    f"/datasets/{_DATASET_ID}/fhirStores/{_FHIR_STORE_ID}/fhir"
)

# Authorized session cache for Healthcare API
_authorized_session: Optional[AuthorizedSession] = None


def _get_authorized_session() -> AuthorizedSession:
    global _authorized_session
    if _authorized_session is None:
        credentials, _ = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        _authorized_session = AuthorizedSession(credentials)
    return _authorized_session


def _default_patient_id(tool_context: ToolContext) -> str:
    patient_id = tool_context.state.get("patient_id") or os.environ.get(
        "DEFAULT_PATIENT_ID", ""
    )
    if not patient_id:
        raise ValueError("patient_id is required in tool context or DEFAULT_PATIENT_ID env var")
    return patient_id


def _fhir_get(resource_type: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    session = _get_authorized_session()
    url = f"{_FHIR_BASE_URL}/{resource_type}"
    headers = {"Accept": "application/fhir+json"}
    response = session.get(url, headers=headers, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"FHIR API error: {response.status_code} {response.text}")
    return response.json()


def _fhir_get_by_id(resource_type: str, resource_id: str) -> Dict[str, Any]:
    session = _get_authorized_session()
    url = f"{_FHIR_BASE_URL}/{resource_type}/{resource_id}"
    headers = {"Accept": "application/fhir+json"}
    response = session.get(url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"FHIR API error: {response.status_code} {response.text}")
    return response.json()


def _bundle_resources(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    resources: List[Dict[str, Any]] = []
    for entry in bundle.get("entry", []):
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if isinstance(resource, dict):
            resources.append(resource)
    return resources


def _patient_everything(patient_id: str) -> List[Dict[str, Any]]:
    """Retrieve the patient compartment and supporting referenced resources."""
    session = _get_authorized_session()
    url = f"{_FHIR_BASE_URL}/Patient/{patient_id}/$everything"
    headers = {"Accept": "application/fhir+json"}
    resources: List[Dict[str, Any]] = []
    pages = 0

    while url and pages < 20:
        response = session.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"FHIR Patient/$everything failed: {response.status_code}")
        bundle = response.json()
        if bundle.get("resourceType") != "Bundle":
            raise RuntimeError("FHIR Patient/$everything did not return a Bundle")
        resources.extend(_bundle_resources(bundle))
        pages += 1

        next_url = None
        for link in bundle.get("link", []):
            if isinstance(link, dict) and link.get("relation") == "next":
                candidate = link.get("url")
                if isinstance(candidate, str) and candidate.startswith(
                    "https://healthcare.googleapis.com/"
                ):
                    next_url = candidate
                break
        url = next_url

    return resources


def _patient_resources_fallback(patient_id: str) -> List[Dict[str, Any]]:
    """Load common patient resources when $everything is unavailable."""

    def fetch(resource_type: str) -> List[Dict[str, Any]]:
        if resource_type == "Patient":
            return [_fhir_get_by_id("Patient", patient_id)]
        try:
            bundle = _fhir_get(
                resource_type,
                {"patient": f"Patient/{patient_id}", "_count": "200"},
            )
        except Exception:
            bundle = _fhir_get(
                resource_type,
                {"subject": f"Patient/{patient_id}", "_count": "200"},
            )
        return _bundle_resources(bundle)

    resources: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch, resource_type): resource_type
            for resource_type in PATIENT_RESOURCE_TYPES
        }
        for future in as_completed(futures):
            try:
                resources.extend(future.result())
            except Exception:
                # Some stores do not support every resource type or patient alias.
                continue
    return resources


def _load_patient_resources(patient_id: str) -> tuple[List[Dict[str, Any]], str]:
    try:
        resources = _patient_everything(patient_id)
        if resources:
            return resources, "FHIR Patient/$everything"
    except Exception:
        pass
    return _patient_resources_fallback(patient_id), "patient-scoped FHIR searches"


def load_patient_resources(patient_id: str) -> tuple[List[Dict[str, Any]], str]:
    """Load the canonical patient compartment for API projections and agent tools."""
    return _load_patient_resources(patient_id)


def _to_citation() -> Dict[str, Any]:
    return {
        "source": f"Healthcare FHIR store: {_PROJECT_ID}/{_LOCATION}/{_DATASET_ID}/{_FHIR_STORE_ID}",
        "last_accessed": "now",
    }


def search_patient_context(
    question: str,
    plan: PatientQueryPlan,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Execute a structured retrieval plan over the active patient's record.

    Use this for every patient-record question. Gemini must interpret the request
    into the typed ``plan`` argument. This tool validates that plan, searches only
    the active patient's FHIR compartment, combines structured and text/fuzzy
    recall, and expands results with prior-turn, encounter, reference, and nearby
    context. Exact operations return complete evidence sets.

    Args:
        question: The user's complete natural-language question.
        plan: Gemini's structured interpretation of intents, concepts, dates,
            scope, and exact operation. FHIR resource types are derived safely.
        tool_context: ADK session context containing the active patient ID.
    """
    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("question is required")
    if len(normalized_question) > 2000:
        raise ValueError("question must be 2000 characters or fewer")

    patient_id = _default_patient_id(tool_context)
    prior_resource_ids = tool_context.state.get("last_patient_resource_ids", [])
    if not isinstance(prior_resource_ids, list):
        prior_resource_ids = []
    resources, retrieval_source = _load_patient_resources(patient_id)
    search_plan = build_search_plan(normalized_question, plan)
    result = build_patient_context(
        normalized_question,
        resources,
        search_plan,
        prior_resource_ids=prior_resource_ids,
    )

    memory_service = get_patient_memory_service()
    semantic_memory = memory_service.retrieve(
        patient_id,
        normalized_question,
    )

    current_topics = [
        item.event.key
        for item in result.relevant_events
        if item.score > 0.01
        and "referenced by the previous conversation turn" not in item.reasons
    ]
    if not current_topics and result.timeline_events:
        current_topics = [event.key for event in result.timeline_events[-12:]]
    if current_topics:
        tool_context.state["last_patient_resource_ids"] = current_topics[:12]
    tool_context.state["last_patient_intents"] = result.plan.intents
    tool_context.state["last_patient_question"] = normalized_question

    resource_by_key = {
        f"{resource.get('resourceType')}/{resource.get('id')}": resource
        for resource in resources
        if resource.get("resourceType") and resource.get("id")
    }
    evidence_keys = [item.event.key for item in result.relevant_events]
    evidence_keys.extend(event.key for event in result.related_context[:10])
    if result.plan.scope == "broad" or result.plan.output_mode == "timeline":
        evidence_keys.extend(event.key for event in result.timeline_events[-15:])
    evidence_keys.extend(
        str(source.get("fhir_key") or "")
        for fact in semantic_memory.get("facts", [])
        for source in fact.get("sources", [])
    )
    evidence_keys = list(dict.fromkeys(key for key in evidence_keys if key))

    terminology_context = get_terminology_enricher().enrich(
        resources,
        fhir_keys=evidence_keys,
        max_concepts=40,
    )
    result_data = result.model_dump(mode="json")
    result_data["semantic_memory"] = semantic_memory
    result_data["terminology_context"] = terminology_context

    deterministic = result_data.get("deterministic_result") or {}
    deterministic_keys = [
        f"{event.get('resource_type')}/{event.get('resource_id')}"
        for event in deterministic.get("events", [])
        if event.get("resource_type") and event.get("resource_id")
    ]
    if deterministic_keys:
        # Exact operators already contain their complete evidence set. Avoid
        # attaching unrelated nearby resources to medication lists and lab
        # series, which previously produced 20-30 noisy citations.
        evidence_keys = list(dict.fromkeys(deterministic_keys))

    sources = deduplicate_sources(
        [
            # Reserve citation capacity for the terminology datasets that
            # validated codes; broad FHIR context can otherwise consume the
            # entire bounded source list before these are appended.
            *terminology_sources(terminology_context),
            *(
                fhir_source(resource_by_key[key])
                for key in evidence_keys
                if key in resource_by_key
            ),
        ]
    )[:30]

    return {
        "data": result_data,
        "patient_id": patient_id,
        "retrieval_source": retrieval_source,
        "citation": _to_citation(),
        "sources": sources,
    }
