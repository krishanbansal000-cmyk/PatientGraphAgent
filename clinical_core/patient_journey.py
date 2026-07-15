"""Deterministic patient journey projection built from canonical FHIR resources."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from clinical_core.context_search import ClinicalEvent, resources_to_events


MEDICATION_TYPES = {"MedicationDispense", "MedicationRequest", "MedicationStatement"}
RESULT_TYPES = {"DiagnosticReport", "Observation"}
DOCUMENT_TYPES = {"Composition", "DocumentReference"}


class JourneyCitation(BaseModel):
    resource_type: str
    resource_id: str
    reference: str
    title: str
    date: Optional[str] = None
    version: Optional[str] = None
    last_updated: Optional[str] = None


class JourneyItem(BaseModel):
    resource_type: str
    resource_id: str
    reference: str
    category: str
    display: str
    summary: str
    status: Optional[str] = None
    date: Optional[str] = None
    code: Optional[str] = None
    value: Optional[float | str] = None
    unit: Optional[str] = None
    interpretation: Optional[str] = None


class JourneyChange(BaseModel):
    kind: str
    category: str
    label: str
    resource_reference: str


class JourneyEpisode(BaseModel):
    id: str
    type: str
    date: Optional[str]
    end_date: Optional[str] = None
    title: str
    status: Optional[str] = None
    encounter_id: Optional[str] = None
    summary: str
    items: List[JourneyItem] = Field(default_factory=list)
    changes: List[JourneyChange] = Field(default_factory=list)
    citations: List[JourneyCitation] = Field(default_factory=list)
    category_counts: Dict[str, int] = Field(default_factory=dict)


class JourneyCurrentState(BaseModel):
    active_conditions: List[JourneyItem] = Field(default_factory=list)
    current_medications: List[JourneyItem] = Field(default_factory=list)
    allergies: List[JourneyItem] = Field(default_factory=list)
    recent_results: List[JourneyItem] = Field(default_factory=list)
    recent_visits: List[JourneyItem] = Field(default_factory=list)


class PatientJourney(BaseModel):
    patient_id: str
    group_id: str
    generated_at: str
    source: str
    current_state: JourneyCurrentState
    episodes: List[JourneyEpisode]
    total_resources: int
    dated_resources: int
    undated_resources: int


def build_patient_journey(
    patient_id: str,
    resources: Sequence[Dict[str, Any]],
    *,
    source: str = "FHIR patient record",
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    episode_types: Sequence[str] = (),
) -> PatientJourney:
    """Build a UI-ready, source-linked journey without inferring clinical causality."""
    resource_by_key = {
        f"{resource.get('resourceType')}/{resource.get('id')}": resource
        for resource in resources
        if resource.get("resourceType") and resource.get("id")
    }
    events = [event for event in resources_to_events(resources) if event.resource_type != "Patient"]

    encounter_events = [event for event in events if event.resource_type == "Encounter"]
    grouped_keys: set[str] = set()
    episode_inputs: List[Tuple[str, List[ClinicalEvent]]] = []

    for encounter in encounter_events:
        linked = [
            event
            for event in events
            if event.key == encounter.key or event.encounter_id == encounter.resource_id
        ]
        linked.sort(key=_event_sort_key)
        grouped_keys.update(event.key for event in linked)
        episode_inputs.append(("encounter", linked))

    standalone: Dict[Tuple[str, str], List[ClinicalEvent]] = defaultdict(list)
    for event in events:
        if event.key in grouped_keys:
            continue
        # References to missing encounters remain visible as standalone events.
        category = _episode_type(event.resource_type)
        date_key = _date_part(event.event_time) or f"undated-{event.key}"
        standalone[(category, date_key)].append(event)

    for (category, _), grouped_events in standalone.items():
        grouped_events.sort(key=_event_sort_key)
        episode_inputs.append((category, grouped_events))

    episodes = [
        _build_episode(kind, grouped_events, resource_by_key)
        for kind, grouped_events in episode_inputs
        if grouped_events
    ]
    episodes.sort(key=_episode_sort_key)
    _apply_changes(episodes, resource_by_key)

    allowed_types = {value.strip().lower() for value in episode_types if value.strip()}
    filtered = [
        episode
        for episode in episodes
        if (not date_start or not episode.date or (_date_part(episode.date) or "") >= date_start)
        and (not date_end or not episode.date or (_date_part(episode.date) or "") <= date_end)
        and (not allowed_types or _episode_matches_types(episode, allowed_types))
    ]
    filtered.sort(key=_episode_sort_key, reverse=True)

    dated_count = sum(1 for event in events if event.event_time)
    return PatientJourney(
        patient_id=patient_id,
        group_id=patient_group_id(patient_id),
        generated_at=datetime.now(timezone.utc).isoformat(),
        source=source,
        current_state=_build_current_state(events, resource_by_key),
        episodes=filtered,
        total_resources=len(resource_by_key),
        dated_resources=dated_count,
        undated_resources=len(events) - dated_count,
    )


def patient_group_id(patient_id: str) -> str:
    """Return a stable Graphiti-compatible partition id without exposing a FHIR id."""
    digest = hashlib.sha256(patient_id.encode("utf-8")).hexdigest()[:24]
    return f"patient_{digest}"


def _build_episode(
    episode_type: str,
    events: Sequence[ClinicalEvent],
    resource_by_key: Dict[str, Dict[str, Any]],
) -> JourneyEpisode:
    encounter = next((event for event in events if event.resource_type == "Encounter"), None)
    primary = encounter or min(events, key=_event_sort_key)
    items = [_to_item(event, resource_by_key.get(event.key, {})) for event in events]
    citations = [_to_citation(event, resource_by_key.get(event.key, {})) for event in events]
    date = primary.event_time or next((event.event_time for event in events if event.event_time), None)
    end_date = None
    if encounter:
        end_date = str(
            (resource_by_key.get(encounter.key, {}).get("period") or {}).get("end") or ""
        ) or None
    category_counts: Dict[str, int] = defaultdict(int)
    for item in items:
        category_counts[item.category] += 1

    identity = "|".join(sorted(event.key for event in events))
    episode_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    title = _episode_title(episode_type, primary, items)
    return JourneyEpisode(
        id=episode_id,
        type=episode_type,
        date=date,
        end_date=end_date,
        title=title,
        status=encounter.status if encounter else primary.status,
        encounter_id=encounter.resource_id if encounter else primary.encounter_id,
        summary=_episode_summary(items),
        items=items,
        citations=citations,
        category_counts=dict(category_counts),
    )


def _build_current_state(
    events: Sequence[ClinicalEvent],
    resource_by_key: Dict[str, Dict[str, Any]],
) -> JourneyCurrentState:
    def items(selected: Iterable[ClinicalEvent], limit: int) -> List[JourneyItem]:
        ordered = sorted(selected, key=_event_sort_key, reverse=True)
        return [_to_item(event, resource_by_key.get(event.key, {})) for event in ordered[:limit]]

    active_conditions = (
        event
        for event in events
        if event.resource_type == "Condition" and event.status in {"active", "recurrence", "relapse"}
    )
    current_medications = (
        event
        for event in events
        if event.resource_type in MEDICATION_TYPES
        and event.status in {"active", "intended", "on-hold", "unknown", None}
    )
    return JourneyCurrentState(
        active_conditions=items(active_conditions, 20),
        current_medications=items(current_medications, 20),
        allergies=items((e for e in events if e.resource_type == "AllergyIntolerance"), 10),
        recent_results=items((e for e in events if e.resource_type in RESULT_TYPES), 12),
        recent_visits=items((e for e in events if e.resource_type == "Encounter"), 10),
    )


def _apply_changes(
    episodes: Sequence[JourneyEpisode],
    resource_by_key: Dict[str, Dict[str, Any]],
) -> None:
    seen_conditions: set[str] = set()
    medication_status: Dict[str, Optional[str]] = {}
    previous_results: Dict[str, Tuple[float, str]] = {}

    for episode in episodes:
        changes: List[JourneyChange] = []
        for item in episode.items:
            identity = (item.code or item.display).strip().lower()
            if item.category == "condition" and identity and identity not in seen_conditions:
                changes.append(
                    JourneyChange(
                        kind="recorded",
                        category="condition",
                        label=f"Condition first recorded: {item.display}",
                        resource_reference=item.reference,
                    )
                )
                seen_conditions.add(identity)
            elif item.category == "medication" and identity:
                previous = medication_status.get(identity)
                if identity not in medication_status:
                    verb = "stopped" if item.status in {"cancelled", "completed", "stopped"} else "recorded"
                    changes.append(
                        JourneyChange(
                            kind=verb,
                            category="medication",
                            label=f"Medication {verb}: {item.display}",
                            resource_reference=item.reference,
                        )
                    )
                elif previous != item.status:
                    changes.append(
                        JourneyChange(
                            kind="status_changed",
                            category="medication",
                            label=f"{item.display} status changed from {previous or 'unknown'} to {item.status or 'unknown'}",
                            resource_reference=item.reference,
                        )
                    )
                medication_status[identity] = item.status
            elif item.category == "result" and identity:
                numeric = _numeric_value(resource_by_key.get(item.reference, {}))
                if numeric is not None and identity in previous_results:
                    old_value, old_unit = previous_results[identity]
                    if numeric != old_value:
                        direction = "increased" if numeric > old_value else "decreased"
                        unit = item.unit or old_unit
                        suffix = f" {unit}" if unit else ""
                        changes.append(
                            JourneyChange(
                                kind=direction,
                                category="result",
                                label=f"{item.display} {direction} from {old_value:g}{suffix} to {numeric:g}{suffix}",
                                resource_reference=item.reference,
                            )
                        )
                if numeric is not None:
                    previous_results[identity] = (numeric, item.unit or "")
        episode.changes = changes


def _to_item(event: ClinicalEvent, resource: Dict[str, Any]) -> JourneyItem:
    value, unit = _resource_value(resource)
    interpretation = None
    interpretation_value = resource.get("interpretation")
    if isinstance(interpretation_value, list) and interpretation_value:
        interpretation = _codeable_text(interpretation_value[0])
    return JourneyItem(
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        reference=event.key,
        category=_item_category(event.resource_type),
        display=event.display,
        summary=event.summary,
        status=event.status,
        date=event.event_time,
        code=event.codes[0] if event.codes else None,
        value=value,
        unit=unit,
        interpretation=interpretation,
    )


def _to_citation(event: ClinicalEvent, resource: Dict[str, Any]) -> JourneyCitation:
    meta = resource.get("meta") if isinstance(resource.get("meta"), dict) else {}
    return JourneyCitation(
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        reference=event.key,
        title=event.display,
        date=event.event_time,
        version=str(meta.get("versionId")) if meta.get("versionId") is not None else None,
        last_updated=event.last_updated,
    )


def _episode_type(resource_type: str) -> str:
    if resource_type in RESULT_TYPES:
        return "result"
    if resource_type in MEDICATION_TYPES:
        return "medication"
    if resource_type == "Condition":
        return "condition"
    if resource_type == "Procedure":
        return "procedure"
    if resource_type == "AllergyIntolerance":
        return "allergy"
    if resource_type == "Immunization":
        return "immunization"
    if resource_type == "CarePlan":
        return "care_plan"
    if resource_type in DOCUMENT_TYPES:
        return "document"
    return "other"


def _item_category(resource_type: str) -> str:
    return "visit" if resource_type == "Encounter" else _episode_type(resource_type)


def _episode_title(episode_type: str, primary: ClinicalEvent, items: Sequence[JourneyItem]) -> str:
    if episode_type == "encounter":
        reason = next((item.display for item in items if item.category == "condition"), None)
        visit_name = primary.display.strip()
        if "visit" not in visit_name.lower() and "encounter" not in visit_name.lower():
            visit_name = f"{visit_name.capitalize()} visit"
        return visit_name if not reason else f"{visit_name}: {reason}"
    labels = {
        "result": "Laboratory and clinical results",
        "medication": "Medication update",
        "condition": "Condition update",
        "procedure": "Procedure",
        "allergy": "Allergy update",
        "immunization": "Immunizations",
        "care_plan": "Care plan updates",
        "document": "Clinical document",
        "other": "Clinical record update",
    }
    if len(items) == 1:
        prefixes = {
            "immunization": "Immunization",
            "care_plan": "Care plan",
        }
        prefix = prefixes.get(episode_type)
        return f"{prefix}: {items[0].display}" if prefix else items[0].display
    return labels.get(episode_type, "Clinical record update")


def _episode_summary(items: Sequence[JourneyItem]) -> str:
    counts: Dict[str, int] = defaultdict(int)
    for item in items:
        counts[item.category] += 1
    labels = {
        "visit": "visit",
        "condition": "condition",
        "medication": "medication",
        "result": "result",
        "procedure": "procedure",
        "allergy": "allergy",
        "immunization": "immunization",
        "care_plan": "care plan",
        "document": "document",
        "other": "record",
    }
    parts = [
        f"{count} {labels.get(category, category)}{'s' if count != 1 else ''}"
        for category, count in counts.items()
    ]
    return ", ".join(parts).capitalize() + "."


def _resource_value(resource: Dict[str, Any]) -> Tuple[Optional[float | str], Optional[str]]:
    quantity = resource.get("valueQuantity")
    if isinstance(quantity, dict) and quantity.get("value") is not None:
        return quantity.get("value"), quantity.get("unit") or quantity.get("code")
    for key in ("valueString", "valueInteger", "valueBoolean"):
        if resource.get(key) is not None:
            return resource.get(key), None
    return None, None


def _numeric_value(resource: Dict[str, Any]) -> Optional[float]:
    value, _ = _resource_value(resource)
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _codeable_text(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    if value.get("text"):
        return str(value["text"])
    coding = value.get("coding")
    if isinstance(coding, list) and coding:
        return str(coding[0].get("display") or coding[0].get("code") or "") or None
    return None


def _date_part(value: Optional[str]) -> Optional[str]:
    return value[:10] if value and len(value) >= 10 else value


def _event_sort_key(event: ClinicalEvent) -> Tuple[str, str]:
    return event.event_time or "", event.key


def _episode_sort_key(episode: JourneyEpisode) -> Tuple[str, str]:
    return episode.date or "0000-00-00", episode.id


def _episode_matches_types(episode: JourneyEpisode, allowed_types: set[str]) -> bool:
    """Match both the episode container and clinical categories within it."""
    return any(
        episode.type == episode_type or episode.category_counts.get(episode_type, 0) > 0
        for episode_type in allowed_types
    )
