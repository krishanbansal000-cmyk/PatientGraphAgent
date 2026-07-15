"""Agent tools that directly call the Google Cloud Healthcare FHIR API.

No SQLite cache, no indexer, no heuristics. The agent constructs FHIR search
queries itself and the tools execute them against the FHIR store.

Also includes:
- normalize_drug via RxNorm
- check_interaction via DDInter 2.0
- get_drug_info via DailyMed FDA labels
- resolve_lab_code via LOINC
"""

import os
from typing import Any, Dict, Optional

from google.adk.tools import ToolContext

from agent_v2.fhir_client import FhirClient
from agent_v2.terminology import RxNormClient, LoincClient
from agent_v2.ddinter import DDInterDatabase
from agent_v2.dailymed import DailyMedClient
from agent_v2.sources import (
    dailymed_source,
    ddinter_source,
    deduplicate_sources,
    fhir_source,
    loinc_source,
    rxnorm_source,
)


_fhir_client: Optional[FhirClient] = None
_rxnorm_client: Optional[RxNormClient] = None
_loinc_client: Optional[LoincClient] = None
_ddinter_db: Optional[DDInterDatabase] = None
_dailymed_client: Optional[DailyMedClient] = None


def _get_fhir_client() -> FhirClient:
    global _fhir_client
    if _fhir_client is None:
        _fhir_client = FhirClient()
    return _fhir_client


def _patient_id(tool_context: ToolContext) -> str:
    pid = tool_context.state.get("patient_id") or os.environ.get("DEFAULT_PATIENT_ID", "")
    if not pid:
        raise ValueError("patient_id is required in tool context or DEFAULT_PATIENT_ID env var")
    return pid


def search_fhir(
    resource_type: str,
    tool_context: ToolContext,
    query: Optional[str] = None,
    patient_scoped: bool = True,
) -> Dict[str, Any]:
    """Search FHIR resources by type. The agent constructs the FHIR search query.

    By default, the patient ID is automatically prepended. Set patient_scoped=False
    for store-wide searches (e.g., listing all patients, aggregating conditions).

    Common FHIR search parameters:
    - code: e.g. "4548-4" (HbA1c LOINC), "316049" (RxNorm)
    - status: "active", "final", "resolved"
    - category: "laboratory", "vital-signs"
    - _count: number of results (default 100)
    - _sort: "-date" for newest first
    - date: "gt2018-01-01", "2018-01-01T00:00:00Z"

    Patient-scoped examples:
      search_fhir("Condition")                          → all conditions for patient
      search_fhir("Observation", "code=4548-4&_sort=-date")  → all HbA1c for patient
      search_fhir("MedicationRequest", "status=active") → active meds for patient
      search_fhir("Encounter", "_sort=-date&_count=3")  → last 3 visits
      search_fhir("Observation", "category=vital-signs")→ vital signs
      search_fhir("AllergyIntolerance")                 → allergies
      search_fhir("Procedure")                          → procedures
      search_fhir("Immunization")                       → immunizations
      search_fhir("CarePlan")                           → care plans

    Store-wide examples (patient_scoped=False):
      search_fhir("Patient", patient_scoped=False)      → list all patients
      search_fhir("Condition", "_count=500", patient_scoped=False) → all conditions in store
      search_fhir("Practitioner", patient_scoped=False) → all practitioners
      search_fhir("Organization", patient_scoped=False) → all organizations

    Args:
        resource_type: FHIR resource type (Condition, Observation, Encounter, etc.)
        query: FHIR search params as "key=value&key=value" (without patient param).
        patient_scoped: If True (default), restrict to active patient. If False, search entire store.
        tool_context: ADK session context.
    """
    client = _get_fhir_client()

    if patient_scoped:
        pid = _patient_id(tool_context)
        params: Dict[str, str] = {"patient": f"Patient/{pid}", "_count": "100"}
    else:
        params = {"_count": "100"}

    if query:
        for pair in query.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k.strip()] = v.strip()

    try:
        resources = client.search_resources(resource_type, params)
        # Return compact summaries, not full JSON, to avoid overwhelming the model
        summaries = []
        for r in resources:
            summaries.append(_summarize_resource(r))
        return {
            "resource_type": resource_type,
            "count": len(resources),
            "resources": summaries,
            "sources": deduplicate_sources(fhir_source(resource) for resource in resources),
        }
    except Exception as e:
        return {"error": str(e)}


def _summarize_resource(r: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key fields from a FHIR resource for a compact summary."""
    summary = {
        "resourceType": r.get("resourceType"),
        "id": r.get("id"),
    }
    # Display name from code/text fields
    for key in ["code", "medicationCodeableConcept", "valueCodeableConcept"]:
        concept = r.get(key)
        if isinstance(concept, dict):
            if concept.get("text"):
                summary["name"] = concept["text"]
                break
            for coding in concept.get("coding", []):
                if coding.get("display"):
                    summary["name"] = coding["display"]
                    summary["code"] = coding.get("code")
                    summary["system"] = coding.get("system")
                    break
            if "name" in summary:
                break
    # Status
    if "status" in r:
        summary["status"] = r["status"]
    clinical = r.get("clinicalStatus")
    if isinstance(clinical, dict):
        for c in clinical.get("coding", []):
            summary["clinicalStatus"] = c.get("code")
            break
    # Dates
    for date_field in ["effectiveDateTime", "authoredOn", "recordedDate", "onsetDateTime", "occurrenceDateTime", "performedDateTime", "date"]:
        if date_field in r:
            summary["date"] = r[date_field]
            break
    period = r.get("period")
    if isinstance(period, dict) and "start" in period:
        summary["date"] = period["start"]
    # Value for Observations
    vq = r.get("valueQuantity")
    if isinstance(vq, dict):
        summary["value"] = vq.get("value")
        summary["unit"] = vq.get("unit")
    # References (so agent knows what to resolve)
    for ref_field in ["subject", "encounter", "requester", "recorder", "performer", "serviceProvider", "context", "author"]:
        ref = r.get(ref_field)
        if isinstance(ref, dict) and "reference" in ref:
            summary[ref_field] = ref["reference"]
        elif isinstance(ref, list) and ref and isinstance(ref[0], dict):
            summary[ref_field] = ref[0].get("reference")
    # Allergy reaction
    reaction = r.get("reaction")
    if isinstance(reaction, list) and reaction:
        manifest = reaction[0].get("manifestation", [{}])
        if isinstance(manifest, list) and manifest:
            summary["reaction"] = manifest[0].get("text", "")
        summary["severity"] = reaction[0].get("severity", "")
    # Encounter class
    cls = r.get("class")
    if isinstance(cls, dict):
        summary["class"] = cls.get("display", cls.get("code"))
    # Encounter reason
    reason = r.get("reasonCode")
    if isinstance(reason, list) and reason:
        summary["reason"] = reason[0].get("text", "")
    # CarePlan title
    if "title" in r:
        summary["title"] = r["title"]
    return summary


def read_fhir(
    resource_type: str,
    resource_id: str,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Read a single FHIR resource by type and ID from the FHIR store.

    Use this to get full details of a specific resource, or to resolve a
    reference like "Practitioner/pract-pcp" (use resource_type="Practitioner",
    resource_id="pract-pcp").

    Examples:
      read_fhir("Condition", "b708d98d-...")     → full Condition resource
      read_fhir("Practitioner", "pract-pcp")     → doctor's name and details
      read_fhir("Encounter", "enc-inpatient-1")  → full encounter
      read_fhir("Observation", "36e4a94f-...")   → full lab result

    Args:
        resource_type: FHIR resource type.
        resource_id: FHIR resource ID.
        tool_context: ADK session context.
    """
    _patient_id(tool_context)  # ensure patient context
    client = _get_fhir_client()
    try:
        resource = client.get_resource(resource_type, resource_id)
        return {"resource": resource, "sources": deduplicate_sources([fhir_source(resource)])}
    except Exception as e:
        return {"error": str(e)}


def resolve_medication(name: str) -> Dict[str, Any]:
    """Normalize a drug name to RxNorm: RxCUI, ingredients, dosage forms.

    Use when the user says a brand name, abbreviation, or misspelled drug name.
    Returns RxCUI codes that can be used in search_fhir with code= parameter.

    Examples:
      resolve_medication("HCTZ")       → rxcui=316049 (Hydrochlorothiazide)
      resolve_medication("Lasix")      → rxcui=316039 (Furosemide)
      resolve_medication("metforman")  → rxcui=6809 (Metformin, typo fix)
      resolve_medication("Glucophage") → rxcui=285065, ingredient=metformin

    Args:
        name: Drug name to resolve (brand, generic, or nickname).
    """
    global _rxnorm_client
    if _rxnorm_client is None:
        _rxnorm_client = RxNormClient()
    result = _rxnorm_client.normalize_drug(name)
    result["sources"] = deduplicate_sources(
        [rxnorm_source(result.get("rxcui"), result.get("canonical_name") or result.get("matched_name") or name)]
    )
    return result


def resolve_lab_code(name_or_code: str) -> Dict[str, Any]:
    """Resolve a lab test name to LOINC concepts, or look up a LOINC code.

    Use when the user says a lab name colloquially or when you have a LOINC code.

    Examples:
      resolve_lab_code("HbA1c")      → loinc_num=4548-4
      resolve_lab_code("A1C")        → loinc_num=4548-4
      resolve_lab_code("4548-4")     → component=Hemoglobin A1c

    Args:
        name_or_code: Lab name or LOINC code.
    """
    global _loinc_client
    if _loinc_client is None:
        _loinc_client = LoincClient()
    if name_or_code and name_or_code[0].isdigit() and "-" in name_or_code:
        details = _loinc_client.get_loinc_details(name_or_code)
        return {
            "query": name_or_code,
            "loinc": details,
            "sources": deduplicate_sources(
                [loinc_source(name_or_code, (details or {}).get("shortname", ""))]
            ),
        }
    results = _loinc_client.search_loinc(name_or_code, max_results=5)
    return {
        "query": name_or_code,
        "loinc_results": results,
        "sources": deduplicate_sources(
            loinc_source(item.get("loinc_num"), item.get("shortname") or item.get("component") or "")
            for item in results
        ),
    }


def check_interaction(drug_a: str, drug_b: str) -> Dict[str, Any]:
    """Check if two drugs interact using DDInter 2.0 database.

    Use drug generic names (not brand names). Use resolve_medication first
    to get the generic name if the user said a brand name.

    Examples:
      check_interaction("metformin", "hydrochlorothiazide") → Moderate
      check_interaction("warfarin", "aspirin") → check severity
      check_interaction("simvastatin", "warfarin") → Minor

    Args:
        drug_a: First drug name (generic preferred).
        drug_b: Second drug name (generic preferred).
    """
    global _ddinter_db
    if _ddinter_db is None:
        _ddinter_db = DDInterDatabase()
    result = _ddinter_db.check_interaction(drug_a, drug_b)
    if result:
        return {
            "interaction": True,
            **result,
            "sources": deduplicate_sources([ddinter_source(result)]),
        }
    no_match = {"drug_a": drug_a, "drug_b": drug_b, "severity": "none"}
    return {
        "interaction": False,
        **no_match,
        "sources": deduplicate_sources([ddinter_source(no_match)]),
    }


def get_drug_info(rxcui: str, drug_name: str = "") -> Dict[str, Any]:
    """Get FDA drug label information from DailyMed by RxCUI or drug name.

    Returns indications, warnings, adverse reactions, dosage, and a
    patient-friendly summary. Pass both RxCUI and drug_name for best results
    (falls back to name search if RxCUI finds nothing).

    Examples:
      get_drug_info("6809") → metformin label info
      get_drug_info("316049", "hydrochlorothiazide") → HCTZ label info
      get_drug_info("", "metformin") → search by name only

    Args:
        rxcui: RxNorm RxCUI code for the drug (optional).
        drug_name: Drug generic name for fallback search (optional).
    """
    global _dailymed_client
    if _dailymed_client is None:
        _dailymed_client = DailyMedClient()
    info = _dailymed_client.get_drug_info(rxcui, drug_name)
    if info:
        info["sources"] = deduplicate_sources([dailymed_source(info)])
        return info
    return {
        "rxcui": rxcui,
        "drug_name": drug_name,
        "error": "No drug label found",
        "sources": [],
    }
