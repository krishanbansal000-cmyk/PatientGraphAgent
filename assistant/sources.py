"""Structured source metadata returned alongside agent tool results."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote


def _compact(source: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in source.items() if value not in (None, "", [])}


def deduplicate_sources(sources: Iterable[Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for source in sources:
        if not source or not source.get("id"):
            continue
        unique.setdefault(str(source["id"]), _compact(source))
    return list(unique.values())


def fhir_source(resource: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    resource_type = str(resource.get("resourceType") or "").strip()
    resource_id = str(resource.get("id") or "").strip()
    if not resource_type or not resource_id:
        return None

    title = _resource_title(resource) or f"{resource_type}/{resource_id}"
    return _compact(
        {
            "id": f"fhir:{resource_type}/{resource_id}",
            "type": "patient_record",
            "title": title,
            "publisher": "Connected FHIR record",
            "resource_type": resource_type,
            "resource_id": resource_id,
            "status": _resource_status(resource),
            "date": _resource_date(resource),
        }
    )


def dailymed_source(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    set_id = str(info.get("set_id") or "").strip()
    if not set_id:
        return None
    return _compact(
        {
            "id": f"dailymed:{set_id}",
            "type": "drug_label",
            "title": info.get("title") or "DailyMed drug label",
            "publisher": "DailyMed / U.S. National Library of Medicine",
            "set_id": set_id,
            "url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=" + quote(set_id),
        }
    )


def rxnorm_source(rxcui: Any, title: str = "") -> Optional[Dict[str, Any]]:
    code = str(rxcui or "").strip()
    if not code:
        return None
    return _compact(
        {
            "id": f"rxnorm:{code}",
            "type": "terminology",
            "title": title or f"RxNorm concept {code}",
            "publisher": "RxNorm / U.S. National Library of Medicine",
            "code": code,
        }
    )


def loinc_source(code: Any, title: str = "") -> Optional[Dict[str, Any]]:
    normalized = str(code or "").strip()
    if not normalized:
        return None
    return _compact(
        {
            "id": f"loinc:{normalized}",
            "type": "terminology",
            "title": title or f"LOINC concept {normalized}",
            "publisher": "LOINC / U.S. National Library of Medicine",
            "code": normalized,
        }
    )


def ddinter_source(interaction: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    drug_a = str(interaction.get("drug_a") or "").strip()
    drug_b = str(interaction.get("drug_b") or "").strip()
    if not drug_a or not drug_b:
        return None
    pair = "|".join(sorted((drug_a.lower(), drug_b.lower())))
    return _compact(
        {
            "id": f"ddinter:{pair}",
            "type": "interaction_database",
            "title": f"{drug_a} and {drug_b} interaction lookup",
            "publisher": "DDInter 2.0 drug interaction database",
            "severity": interaction.get("severity"),
            "ddinter_id_a": interaction.get("ddinter_id_a"),
            "ddinter_id_b": interaction.get("ddinter_id_b"),
        }
    )


def _resource_title(resource: Dict[str, Any]) -> str:
    for key in ("code", "medicationCodeableConcept", "valueCodeableConcept"):
        concept = resource.get(key)
        if not isinstance(concept, dict):
            continue
        if concept.get("text"):
            return str(concept["text"])
        for coding in concept.get("coding", []):
            if isinstance(coding, dict) and coding.get("display"):
                return str(coding["display"])

    resource_type = resource.get("resourceType")
    if resource_type == "Encounter":
        class_value = resource.get("class")
        if isinstance(class_value, dict):
            return str(class_value.get("display") or class_value.get("code") or "Encounter")
    if resource_type == "Organization" and resource.get("name"):
        return str(resource["name"])
    return ""


def _resource_status(resource: Dict[str, Any]) -> str:
    if resource.get("status"):
        return str(resource["status"])
    clinical_status = resource.get("clinicalStatus")
    if isinstance(clinical_status, dict):
        for coding in clinical_status.get("coding", []):
            if isinstance(coding, dict) and coding.get("code"):
                return str(coding["code"])
    return ""


def _resource_date(resource: Dict[str, Any]) -> str:
    for key in (
        "effectiveDateTime",
        "authoredOn",
        "recordedDate",
        "onsetDateTime",
        "occurrenceDateTime",
        "performedDateTime",
        "date",
    ):
        if resource.get(key):
            return str(resource[key])
    period = resource.get("period")
    if isinstance(period, dict) and period.get("start"):
        return str(period["start"])
    return ""
