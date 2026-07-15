"""Hybrid structured and keyword retrieval over one patient's FHIR record."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field, field_validator


SearchIntent = Literal[
    "medication",
    "condition",
    "result",
    "visit",
    "procedure",
    "allergy",
    "immunization",
    "symptom",
    "document",
    "general",
]


PATIENT_RESOURCE_TYPES: Tuple[str, ...] = (
    "Patient",
    "AllergyIntolerance",
    "CarePlan",
    "ClinicalImpression",
    "Composition",
    "Condition",
    "DiagnosticReport",
    "DocumentReference",
    "Encounter",
    "Goal",
    "Immunization",
    "MedicationDispense",
    "MedicationRequest",
    "MedicationStatement",
    "Observation",
    "Procedure",
    "QuestionnaireResponse",
    "ServiceRequest",
)

_INTENT_RESOURCES: Dict[str, Tuple[str, ...]] = {
    "medication": (
        "MedicationRequest",
        "MedicationStatement",
        "MedicationDispense",
    ),
    "condition": ("Condition",),
    "result": ("Observation", "DiagnosticReport"),
    "visit": ("Encounter",),
    "procedure": ("Procedure",),
    "allergy": ("AllergyIntolerance",),
    "immunization": ("Immunization",),
    "symptom": (
        "Condition",
        "Observation",
        "ClinicalImpression",
        "QuestionnaireResponse",
    ),
    "document": ("DocumentReference", "DiagnosticReport"),
    "general": PATIENT_RESOURCE_TYPES,
}

_STOP_WORDS = {
    "a",
    "about",
    "after",
    "all",
    "and",
    "any",
    "at",
    "be",
    "before",
    "between",
    "chronological",
    "data",
    "did",
    "do",
    "everything",
    "explain",
    "for",
    "from",
    "general",
    "give",
    "happen",
    "happened",
    "has",
    "have",
    "how",
    "historical",
    "history",
    "i",
    "in",
    "is",
    "it",
    "last",
    "latest",
    "me",
    "month",
    "my",
    "of",
    "on",
    "other",
    "overview",
    "please",
    "previous",
    "query",
    "recent",
    "show",
    "since",
    "summarize",
    "things",
    "that",
    "the",
    "this",
    "time",
    "timeline",
    "till",
    "tell",
    "to",
    "through",
    "until",
    "was",
    "what",
    "when",
    "why",
    "with",
}

_GENERIC_MEDICATION_TERMS = {
    "drug",
    "dose",
    "medicine",
    "medication",
    "pill",
    "prescription",
    "tablet",
}


class PatientSearchPlan(BaseModel):
    """Validated internal retrieval plan derived from the model tool argument."""

    intent: SearchIntent
    intents: List[SearchIntent]
    query_terms: List[str]
    expanded_terms: List[str]
    resource_types: List[str]
    time_scope: Literal["latest", "historical", "all"] = "all"
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    output_mode: Literal["direct", "summary", "timeline"] = "direct"
    scope: Literal["focused", "broad"] = "focused"
    references_prior_context: bool = False
    operation: Literal["search", "current_medications", "lab_series", "timeline"] = "search"
    target_code: Optional[str] = None


class PatientQueryPlan(BaseModel):
    """Structured query interpretation supplied by Gemini in the tool call."""

    intents: List[SearchIntent] = Field(
        min_length=1,
        description="Every clinical category needed to answer the question.",
    )
    concepts: List[str] = Field(
        default_factory=list,
        description="Normalized clinical concepts and useful aliases from the request.",
    )
    time_scope: Literal["latest", "historical", "all"] = Field(
        default="all",
        description="Whether recent, historical, or all matching records are requested.",
    )
    date_start: Optional[str] = Field(
        default=None,
        description="Inclusive ISO date boundary (YYYY-MM-DD), when specified.",
    )
    date_end: Optional[str] = Field(
        default=None,
        description="Inclusive ISO date boundary (YYYY-MM-DD), when specified.",
    )
    output_mode: Literal["direct", "summary", "timeline"] = Field(
        default="direct",
        description="Requested answer organization.",
    )
    scope: Literal["focused", "broad"] = Field(
        default="focused",
        description="Focused concept retrieval or broad patient-record coverage.",
    )
    references_prior_context: bool = Field(
        default=False,
        description="True when the request refers to evidence from an earlier turn.",
    )
    operation: Literal["search", "current_medications", "lab_series", "timeline"] = Field(
        default="search",
        description="Exact deterministic operation, or search for normal retrieval.",
    )
    target_code: Optional[str] = Field(
        default=None,
        description="Validated terminology code; omit unless supplied or resolved.",
    )

    @field_validator("date_start", "date_end")
    @classmethod
    def validate_iso_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return date.fromisoformat(value).isoformat()


class ClinicalEvent(BaseModel):
    """Searchable projection of one FHIR resource."""

    resource_type: str
    resource_id: str
    display: str
    summary: str
    event_time: Optional[str] = None
    event_time_kind: Optional[str] = None
    recorded_time: Optional[str] = None
    last_updated: Optional[str] = None
    encounter_id: Optional[str] = None
    status: Optional[str] = None
    codes: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)
    search_text: str = ""

    @property
    def key(self) -> str:
        return f"{self.resource_type}/{self.resource_id}"


class PatientSnapshot(BaseModel):
    """Deterministic, source-linked overview derived from current FHIR data."""

    active_conditions: List[ClinicalEvent] = Field(default_factory=list)
    current_medications: List[ClinicalEvent] = Field(default_factory=list)
    allergies: List[ClinicalEvent] = Field(default_factory=list)
    recent_results: List[ClinicalEvent] = Field(default_factory=list)
    recent_visits: List[ClinicalEvent] = Field(default_factory=list)


class RankedClinicalEvent(BaseModel):
    """FHIR event with retrieval score and evidence reasons."""

    event: ClinicalEvent
    score: float
    reasons: List[str] = Field(default_factory=list)


class DeterministicResult(BaseModel):
    """Complete, ordered evidence for common exact patient-record operations."""

    kind: Literal["lab_series", "current_medications"]
    label: str
    code: Optional[str] = None
    complete: bool = True
    events: List[ClinicalEvent] = Field(default_factory=list)


class PatientContextResult(BaseModel):
    """Single context package returned to the patient agent."""

    query: str
    plan: PatientSearchPlan
    resolution_status: Literal["resolved", "ambiguous", "not_found"]
    clarification_question: Optional[str] = None
    relevant_events: List[RankedClinicalEvent]
    related_context: List[ClinicalEvent]
    essential_context: List[ClinicalEvent]
    timeline_events: List[ClinicalEvent]
    patient_snapshot: PatientSnapshot
    deterministic_result: Optional[DeterministicResult] = None
    retrieval_modes: List[str]
    total_resources_considered: int
    date_filtered_resources: int
    undated_resources: int
    conversation_context_used: bool = False


def build_patient_context(
    question: str,
    resources: Sequence[Dict[str, Any]],
    plan: PatientSearchPlan,
    *,
    prior_resource_ids: Sequence[str] = (),
    max_results: int = 10,
    max_related: int = 12,
) -> PatientContextResult:
    """Build a fused, context-expanded evidence package for one question."""
    events = resources_to_events(resources)
    if not events:
        return PatientContextResult(
            query=question,
            plan=plan,
            resolution_status="not_found",
            clarification_question=None,
            relevant_events=[],
            related_context=[],
            essential_context=[],
            timeline_events=[],
            patient_snapshot=PatientSnapshot(),
            deterministic_result=None,
            retrieval_modes=["structured", "keyword", "fuzzy"],
            total_resources_considered=0,
            date_filtered_resources=0,
            undated_resources=0,
            conversation_context_used=False,
        )

    filtered_events = _filter_events_by_date(events, plan)
    with ThreadPoolExecutor(max_workers=2) as executor:
        structured_future = executor.submit(_structured_search, plan, filtered_events)
        keyword_future = executor.submit(_keyword_search, plan, filtered_events)
        structured_results = structured_future.result()
        keyword_results = keyword_future.result()

    relevant = _fuse_rankings(
        structured_results,
        keyword_results,
        max_results=max_results,
    )
    conversation_context_used = False
    if plan.references_prior_context and prior_resource_ids:
        relevant, conversation_context_used = _inject_prior_context(
            relevant,
            filtered_events if plan.date_start or plan.date_end else events,
            prior_resource_ids,
            max_results=max_results,
        )
    had_search_matches = bool(relevant)
    if not relevant:
        relevant = _recent_fallback(filtered_events, plan, max_results=max_results)

    related = _expand_context(
        events,
        [item.event for item in relevant],
        max_related=max_related,
    )
    essential = _essential_context(events)
    snapshot = _patient_snapshot(events)
    deterministic_result = _deterministic_result(plan, events, snapshot)
    timeline = _timeline_events(filtered_events, plan)
    resolution_status, clarification = _resolution_state(plan, relevant, essential)
    if not had_search_matches and plan.query_terms:
        resolution_status = "not_found"
        clarification = None

    return PatientContextResult(
        query=question,
        plan=plan,
        resolution_status=resolution_status,
        clarification_question=clarification,
        relevant_events=relevant,
        related_context=related,
        essential_context=essential,
        timeline_events=timeline,
        patient_snapshot=snapshot,
        deterministic_result=deterministic_result,
        retrieval_modes=["structured", "keyword", "fuzzy"],
        total_resources_considered=len(events),
        date_filtered_resources=len(filtered_events),
        undated_resources=sum(1 for event in events if not event.event_time),
        conversation_context_used=conversation_context_used,
    )


def build_search_plan(question: str, interpreted: PatientQueryPlan) -> PatientSearchPlan:
    """Validate and materialize Gemini's structured tool argument for execution."""
    # ADK publishes the nested Pydantic schema to Gemini but currently invokes
    # function tools with the nested argument decoded as a plain dictionary.
    interpreted = PatientQueryPlan.model_validate(interpreted)
    intents = list(dict.fromkeys(interpreted.intents)) or ["general"]
    requested_types = list(
        dict.fromkeys(
            resource_type
            for intent in intents
            for resource_type in _INTENT_RESOURCES[intent]
        )
    )

    query_terms = [
        token for token in _tokenize(question) if token not in _STOP_WORDS
    ]
    expanded_terms = list(query_terms)
    for concept in interpreted.concepts:
        normalized = concept.strip().lower()
        if normalized:
            expanded_terms.append(normalized)
            expanded_terms.extend(_tokenize(normalized))

    date_start = interpreted.date_start
    date_end = interpreted.date_end
    if date_start and date_end and date_start > date_end:
        raise ValueError("date_start must be on or before date_end")

    operation = interpreted.operation
    output_mode = "timeline" if operation == "timeline" else interpreted.output_mode
    scope = "broad" if operation == "timeline" else interpreted.scope
    return PatientSearchPlan(
        intent=intents[0],
        intents=intents,
        query_terms=list(dict.fromkeys(query_terms)),
        expanded_terms=list(dict.fromkeys(expanded_terms)),
        resource_types=list(dict.fromkeys(requested_types)),
        time_scope=interpreted.time_scope,
        date_start=date_start,
        date_end=date_end,
        output_mode=output_mode,
        scope=scope,
        references_prior_context=interpreted.references_prior_context,
        operation=operation,
        target_code=(interpreted.target_code or "").strip() or None,
    )


def _filter_events_by_date(
    events: Sequence[ClinicalEvent], plan: PatientSearchPlan
) -> List[ClinicalEvent]:
    if not plan.date_start and not plan.date_end:
        return list(events)
    start = _parse_time(plan.date_start)
    end = _parse_time(plan.date_end)
    filtered: List[ClinicalEvent] = []
    for event in events:
        if event.event_time_kind == "FHIR last updated":
            continue
        event_time = _parse_time(event.event_time)
        if event_time is None:
            continue
        if start and event_time < start:
            continue
        if end and event_time > end.replace(hour=23, minute=59, second=59):
            continue
        filtered.append(event)
    return filtered


def _inject_prior_context(
    relevant: Sequence[RankedClinicalEvent],
    events: Sequence[ClinicalEvent],
    prior_resource_ids: Sequence[str],
    *,
    max_results: int,
) -> Tuple[List[RankedClinicalEvent], bool]:
    by_key = {event.key: event for event in events}
    prior: List[RankedClinicalEvent] = []
    for resource_id in prior_resource_ids:
        event = by_key.get(resource_id)
        if event is None:
            continue
        prior.append(
            RankedClinicalEvent(
                event=event,
                score=1.0,
                reasons=["referenced by the previous conversation turn"],
            )
        )
    if not prior:
        return list(relevant), False
    prior_keys = {item.event.key for item in prior}
    combined = [*prior, *(item for item in relevant if item.event.key not in prior_keys)]
    return combined[:max_results], True


def _timeline_events(
    events: Sequence[ClinicalEvent], plan: PatientSearchPlan, *, limit: int = 60
) -> List[ClinicalEvent]:
    if plan.output_mode != "timeline" and plan.scope != "broad":
        return []
    dated = [
        event
        for event in events
        if event.event_time
        and event.event_time_kind != "FHIR last updated"
        and event.resource_type != "Patient"
    ]
    dated.sort(key=_event_timestamp)
    return dated[-limit:]


def _patient_snapshot(events: Sequence[ClinicalEvent]) -> PatientSnapshot:
    def recent(resource_types: Set[str], limit: int) -> List[ClinicalEvent]:
        selected = [event for event in events if event.resource_type in resource_types]
        selected.sort(key=_event_timestamp, reverse=True)
        return selected[:limit]

    active_conditions = [
        event
        for event in events
        if event.resource_type == "Condition" and event.status == "active"
    ]
    active_conditions.sort(key=_event_timestamp, reverse=True)
    current_medications = [
        event
        for event in events
        if event.resource_type in set(_INTENT_RESOURCES["medication"])
        and event.status in {"active", "intended", "on-hold", "unknown", None}
    ]
    current_medications.sort(key=_event_timestamp, reverse=True)
    return PatientSnapshot(
        active_conditions=active_conditions[:20],
        current_medications=current_medications[:20],
        allergies=recent({"AllergyIntolerance"}, 10),
        recent_results=recent({"Observation", "DiagnosticReport"}, 12),
        recent_visits=recent({"Encounter"}, 10),
    )


def _deterministic_result(
    plan: PatientSearchPlan,
    events: Sequence[ClinicalEvent],
    snapshot: PatientSnapshot,
) -> Optional[DeterministicResult]:
    """Return a complete evidence set when generic top-N ranking is unsafe."""
    if plan.operation == "current_medications":
        return DeterministicResult(
            kind="current_medications",
            label="Current medications recorded in FHIR",
            events=snapshot.current_medications,
        )

    if plan.operation != "lab_series":
        return None

    observations = [event for event in events if event.resource_type == "Observation"]
    if not observations:
        return None

    available_codes = {code for event in observations for code in event.codes}
    selected_code = plan.target_code if plan.target_code in available_codes else None
    if selected_code is None:
        terms = {term for term in plan.expanded_terms if len(term) > 2}
        code_scores: Dict[str, float] = {}
        for event in observations:
            event_tokens = set(_tokenize(f"{event.display} {event.summary}"))
            direct_hits = len(terms.intersection(event_tokens))
            fuzzy_hits = len(_fuzzy_matches(terms, event_tokens))
            score = float(direct_hits) + (0.75 * fuzzy_hits)
            if score <= 0:
                continue
            for code in event.codes:
                code_scores[code] = code_scores.get(code, 0.0) + score
        if code_scores:
            selected_code = max(code_scores, key=code_scores.get)

    if selected_code is None:
        return None

    series = [event for event in observations if selected_code in event.codes]
    if not series:
        return None
    series.sort(key=_event_timestamp)
    return DeterministicResult(
        kind="lab_series",
        label=series[0].display,
        code=selected_code,
        events=series,
    )


def resources_to_events(
    resources: Sequence[Dict[str, Any]],
) -> List[ClinicalEvent]:
    """Convert FHIR resources into searchable clinical-event projections."""
    events: List[ClinicalEvent] = []
    seen: Set[str] = set()
    for resource in resources:
        resource_type = str(resource.get("resourceType") or "")
        resource_id = str(resource.get("id") or "")
        if not resource_type or not resource_id:
            continue
        key = f"{resource_type}/{resource_id}"
        if key in seen:
            continue
        seen.add(key)

        display = _resource_display(resource)
        summary = _resource_summary(resource, display)
        strings = _collect_strings(resource)
        event_time, event_time_kind, recorded_time, last_updated = _resource_dates(
            resource
        )
        search_text = " ".join(dict.fromkeys([display, summary, *strings]))[:12000]
        events.append(
            ClinicalEvent(
                resource_type=resource_type,
                resource_id=resource_id,
                display=display,
                summary=summary,
                event_time=event_time,
                event_time_kind=event_time_kind,
                recorded_time=recorded_time,
                last_updated=last_updated,
                encounter_id=_encounter_id(resource),
                status=_resource_status(resource),
                codes=_resource_codes(resource),
                references=sorted(set(_resource_references(resource))),
                search_text=search_text,
            )
        )
    return events


def _structured_search(
    plan: PatientSearchPlan, events: Sequence[ClinicalEvent]
) -> List[RankedClinicalEvent]:
    results: List[RankedClinicalEvent] = []
    requested_types = set(plan.resource_types)
    terms = {term.lower() for term in plan.expanded_terms}
    for event in events:
        score = 0.0
        reasons: List[str] = []
        type_match_is_meaningful = plan.intent != "general" or not plan.query_terms
        if event.resource_type in requested_types and type_match_is_meaningful:
            score += 5.0
            reasons.append("resource type matches a requested clinical category")

        structured_text = " ".join(
            [event.display, event.status or "", *event.codes]
        ).lower()
        exact_terms = sorted(term for term in terms if term and term in structured_text)
        if exact_terms:
            score += min(8.0, 3.0 + (2.0 * len(exact_terms)))
            reasons.append(f"structured fields match: {', '.join(exact_terms[:4])}")

        if score > 0 and plan.time_scope == "latest" and event.event_time:
            score += 1.5
            reasons.append("dated event supports latest/recent request")
        if (
            "medication" in plan.intents
            and event.resource_type in _INTENT_RESOURCES["medication"]
            and event.status in {"active", "intended"}
        ):
            score += 1.5
            reasons.append("current medication status")
        if (
            "condition" in plan.intents
            and event.resource_type == "Condition"
            and event.status == "active"
        ):
            score += 1.5
            reasons.append("active condition status")

        if score > 0:
            results.append(RankedClinicalEvent(event=event, score=score, reasons=reasons))

    return sorted(results, key=_rank_sort_key, reverse=True)


def _keyword_search(
    plan: PatientSearchPlan, events: Sequence[ClinicalEvent]
) -> List[RankedClinicalEvent]:
    query_tokens = set(plan.query_terms)
    expanded_tokens = set(_tokens_from_terms(plan.expanded_terms))
    results: List[RankedClinicalEvent] = []

    for event in events:
        event_tokens = set(_tokenize(event.search_text))
        direct_overlap = query_tokens & event_tokens
        expanded_overlap = expanded_tokens & event_tokens
        score = (4.0 * len(direct_overlap)) + (1.75 * len(expanded_overlap))
        reasons: List[str] = []
        if direct_overlap:
            reasons.append(f"keyword match: {', '.join(sorted(direct_overlap)[:4])}")
        if expanded_overlap - direct_overlap:
            reasons.append(
                "related clinical terms: "
                + ", ".join(sorted(expanded_overlap - direct_overlap)[:4])
            )

        fuzzy_matches = _fuzzy_matches(query_tokens, event_tokens)
        if fuzzy_matches:
            fuzzy_score = max(match[2] for match in fuzzy_matches)
            score += 4.0 * fuzzy_score
            reasons.append(
                "approximate term match: "
                + ", ".join(f"{left}->{right}" for left, right, _ in fuzzy_matches[:2])
            )

        if score > 0:
            results.append(RankedClinicalEvent(event=event, score=score, reasons=reasons))

    return sorted(results, key=_rank_sort_key, reverse=True)


def _fuse_rankings(
    structured: Sequence[RankedClinicalEvent],
    keyword: Sequence[RankedClinicalEvent],
    *,
    max_results: int,
) -> List[RankedClinicalEvent]:
    fused: Dict[str, RankedClinicalEvent] = {}
    for weight, ranking in ((1.15, structured), (1.0, keyword)):
        for rank, item in enumerate(ranking, start=1):
            contribution = weight / (10.0 + rank)
            contribution += min(item.score, 12.0) / 100.0
            existing = fused.get(item.event.key)
            if existing is None:
                fused[item.event.key] = RankedClinicalEvent(
                    event=item.event,
                    score=contribution,
                    reasons=list(item.reasons),
                )
            else:
                existing.score += contribution
                existing.reasons = list(dict.fromkeys([*existing.reasons, *item.reasons]))

    ranked = sorted(fused.values(), key=_rank_sort_key, reverse=True)
    return ranked[:max_results]


def _expand_context(
    events: Sequence[ClinicalEvent],
    selected: Sequence[ClinicalEvent],
    *,
    max_related: int,
) -> List[ClinicalEvent]:
    if not selected:
        return []
    selected_keys = {event.key for event in selected}
    encounter_ids = {event.encounter_id for event in selected if event.encounter_id}
    selected_references = {reference for event in selected for reference in event.references}
    selected_times = [parsed for event in selected if (parsed := _parse_time(event.event_time))]

    scored: List[Tuple[float, ClinicalEvent]] = []
    for event in events:
        if event.key in selected_keys:
            continue
        score = 0.0
        if event.encounter_id and event.encounter_id in encounter_ids:
            score += 7.0
        if event.key in selected_references:
            score += 6.0
        if selected_keys.intersection(event.references):
            score += 5.0
        event_time = _parse_time(event.event_time)
        if event_time and selected_times:
            nearest_days = min(
                abs((event_time - selected_time).total_seconds()) / 86400
                for selected_time in selected_times
            )
            if nearest_days <= 7:
                score += 3.0
            elif nearest_days <= 30:
                score += 1.5
        if score > 0:
            scored.append((score, event))

    scored.sort(key=lambda item: (item[0], _event_timestamp(item[1])), reverse=True)
    return [event for _, event in scored[:max_related]]


def _essential_context(events: Sequence[ClinicalEvent]) -> List[ClinicalEvent]:
    selected: List[ClinicalEvent] = []
    limits = {
        "AllergyIntolerance": 5,
        "Condition": 5,
        "MedicationRequest": 5,
        "MedicationStatement": 3,
        "Encounter": 1,
        "Observation": 3,
    }
    for resource_type, limit in limits.items():
        candidates = [event for event in events if event.resource_type == resource_type]
        if resource_type in {"Condition", "MedicationRequest", "MedicationStatement"}:
            active = [event for event in candidates if event.status == "active"]
            candidates = active or candidates
        candidates.sort(key=_event_timestamp, reverse=True)
        selected.extend(candidates[:limit])
    return selected


def _resolution_state(
    plan: PatientSearchPlan,
    relevant: Sequence[RankedClinicalEvent],
    essential: Sequence[ClinicalEvent],
) -> Tuple[Literal["resolved", "ambiguous", "not_found"], Optional[str]]:
    if not relevant:
        return "not_found", None
    if plan.intent != "medication" or len(plan.intents) > 1:
        return "resolved", None

    medication_events = [
        item.event
        for item in relevant
        if item.event.resource_type
        in {"MedicationDispense", "MedicationRequest", "MedicationStatement"}
    ]
    if not medication_events:
        medication_events = [
            event
            for event in essential
            if event.resource_type
            in {"MedicationDispense", "MedicationRequest", "MedicationStatement"}
        ]
    names = list(dict.fromkeys(event.display for event in medication_events if event.display))

    specific_terms = set(plan.query_terms) - _GENERIC_MEDICATION_TERMS
    directly_matched = [
        name
        for name in names
        if specific_terms.intersection(_tokenize(name))
        or _fuzzy_matches(specific_terms, set(_tokenize(name)))
    ]
    if len(directly_matched) == 1 or len(names) == 1:
        return "resolved", None
    if len(directly_matched) > 1:
        choices = directly_matched[:3]
    elif len(names) > 1:
        choices = names[:3]
    else:
        return "not_found", None
    return "ambiguous", "Which medicine do you mean: " + ", ".join(choices) + "?"


def _recent_fallback(
    events: Sequence[ClinicalEvent],
    plan: PatientSearchPlan,
    *,
    max_results: int,
) -> List[RankedClinicalEvent]:
    candidates = [event for event in events if event.resource_type in plan.resource_types]
    candidates.sort(key=_event_timestamp, reverse=True)
    return [
        RankedClinicalEvent(
            event=event,
            score=0.01,
            reasons=["recent resource fallback for the interpreted intent"],
        )
        for event in candidates[:max_results]
    ]


def _resource_display(resource: Dict[str, Any]) -> str:
    for path in (
        ("medicationCodeableConcept", "text"),
        ("vaccineCode", "text"),
        ("code", "text"),
        ("type", "text"),
    ):
        value = _nested_value(resource, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    type_display = _first_codeable_text(resource.get("type"))
    if type_display:
        return type_display
    vaccine_display = _first_codeable_text(resource.get("vaccineCode"))
    if vaccine_display:
        return vaccine_display
    for key in ("title", "description", "conclusion"):
        value = resource.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if resource.get("resourceType") == "Encounter":
        encounter_class = resource.get("class", {})
        return str(encounter_class.get("display") or encounter_class.get("code") or "Encounter")
    if resource.get("resourceType") == "Patient":
        return "Patient record"
    return str(resource.get("resourceType") or "FHIR resource")


def _resource_summary(resource: Dict[str, Any], display: str) -> str:
    resource_type = str(resource.get("resourceType") or "FHIR resource")
    parts = [display]
    status = _resource_status(resource)
    if status:
        parts.append(f"status {status}")
    if resource_type == "Observation":
        value = resource.get("valueQuantity", {})
        if value.get("value") is not None:
            parts.append(f"value {value.get('value')} {value.get('unit') or ''}".strip())
        elif resource.get("valueString"):
            parts.append(f"value {resource['valueString']}")
    if resource_type == "Encounter":
        reason = _first_codeable_text(resource.get("reasonCode"))
        if reason:
            parts.append(f"reason {reason}")
    if resource_type.startswith("Medication"):
        dosage = resource.get("dosageInstruction") or resource.get("dosage")
        if isinstance(dosage, list) and dosage:
            dosage_text = dosage[0].get("text") if isinstance(dosage[0], dict) else None
            if dosage_text:
                parts.append(f"dosage {dosage_text}")
    event_time, event_time_kind, _, _ = _resource_dates(resource)
    if event_time:
        parts.append(f"{event_time_kind or 'event date'} {event_time}")
    return "; ".join(parts)


def _resource_dates(
    resource: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Return primary clinical time, its meaning, recorded time, and update time."""
    resource_type = str(resource.get("resourceType") or "")
    candidates: Dict[str, Tuple[Tuple[str, str], ...]] = {
        "AllergyIntolerance": (("onsetDateTime", "onset"),),
        "CarePlan": (
            ("period.start", "care plan start"),
            ("created", "care plan created"),
        ),
        "ClinicalImpression": (("date", "assessment date"),),
        "Composition": (("date", "document date"),),
        "Condition": (
            ("onsetDateTime", "onset"),
            ("onsetPeriod.start", "onset"),
        ),
        "DiagnosticReport": (
            ("effectiveDateTime", "effective date"),
            ("effectivePeriod.start", "effective date"),
            ("issued", "issued date"),
        ),
        "DocumentReference": (("date", "document date"),),
        "Encounter": (("period.start", "visit start"),),
        "Goal": (("startDate", "goal start"),),
        "Immunization": (("occurrenceDateTime", "administered date"),),
        "MedicationDispense": (("whenHandedOver", "dispensed date"),),
        "MedicationRequest": (("authoredOn", "ordered date"),),
        "MedicationStatement": (
            ("effectiveDateTime", "effective date"),
            ("effectivePeriod.start", "effective date"),
            ("dateAsserted", "asserted date"),
        ),
        "Observation": (
            ("effectiveDateTime", "observed date"),
            ("effectivePeriod.start", "observed date"),
            ("issued", "issued date"),
        ),
        "Procedure": (
            ("performedDateTime", "performed date"),
            ("performedPeriod.start", "performed date"),
        ),
        "QuestionnaireResponse": (("authored", "authored date"),),
        "ServiceRequest": (
            ("occurrenceDateTime", "requested date"),
            ("authoredOn", "ordered date"),
        ),
    }
    event_time = None
    event_time_kind = None
    for path, kind in candidates.get(resource_type, ()):
        value = _path_value(resource, path)
        if isinstance(value, str) and value:
            event_time = value
            event_time_kind = kind
            break

    recorded_time = resource.get("recordedDate")
    if not isinstance(recorded_time, str):
        recorded_time = None
    if event_time is None and recorded_time:
        event_time = recorded_time
        event_time_kind = "recorded date"

    if event_time is None:
        for key, kind in (
            ("date", "event date"),
            ("authoredOn", "authored date"),
            ("issued", "issued date"),
            ("birthDate", "birth date"),
        ):
            value = resource.get(key)
            if isinstance(value, str):
                event_time = value
                event_time_kind = kind
                break

    meta = resource.get("meta")
    last_updated = meta.get("lastUpdated") if isinstance(meta, dict) else None
    if not isinstance(last_updated, str):
        last_updated = None
    if event_time is None and last_updated:
        event_time = last_updated
        event_time_kind = "FHIR last updated"
    return event_time, event_time_kind, recorded_time, last_updated


def _resource_status(resource: Dict[str, Any]) -> Optional[str]:
    status = resource.get("status")
    if isinstance(status, str):
        return status.lower()
    clinical_status = resource.get("clinicalStatus")
    if isinstance(clinical_status, str):
        return clinical_status.lower()
    if isinstance(clinical_status, dict):
        coding = clinical_status.get("coding")
        if isinstance(coding, list) and coding and isinstance(coding[0], dict):
            code = coding[0].get("code")
            if isinstance(code, str):
                return code.lower()
    return None


def _resource_codes(resource: Dict[str, Any]) -> List[str]:
    codes: List[str] = []
    for node in _walk_nodes(resource):
        coding = node.get("coding")
        if not isinstance(coding, list):
            continue
        for item in coding:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            display = item.get("display")
            if isinstance(code, str):
                codes.append(code)
            if isinstance(display, str):
                codes.append(display)
    return list(dict.fromkeys(codes))


def _resource_references(resource: Dict[str, Any]) -> Iterable[str]:
    for node in _walk_nodes(resource):
        reference = node.get("reference")
        if isinstance(reference, str):
            normalized = _normalize_reference(reference)
            if normalized:
                yield normalized


def _encounter_id(resource: Dict[str, Any]) -> Optional[str]:
    if resource.get("resourceType") == "Encounter" and resource.get("id"):
        return str(resource["id"])
    encounter = resource.get("encounter")
    if isinstance(encounter, dict):
        reference = _normalize_reference(str(encounter.get("reference") or ""))
        if reference and reference.startswith("Encounter/"):
            return reference.split("/", 1)[1]
    return None


def _collect_strings(value: Any) -> List[str]:
    strings: List[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"id", "system", "url", "versionId"}:
                continue
            strings.extend(_collect_strings(child))
    elif isinstance(value, list):
        for child in value:
            strings.extend(_collect_strings(child))
    elif isinstance(value, str) and value.strip():
        strings.append(value.strip())
    return strings


def _walk_nodes(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_nodes(child)


def _normalize_reference(reference: str) -> Optional[str]:
    reference = reference.split("/_history/", 1)[0].rstrip("/")
    parts = reference.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[-2:])


def _nested_value(value: Dict[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _path_value(value: Dict[str, Any], path: str) -> Any:
    return _nested_value(value, path.split("."))


def _first_codeable_text(value: Any) -> Optional[str]:
    if isinstance(value, list):
        for item in value:
            text = _first_codeable_text(item)
            if text:
                return text
        return None
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        coding = value.get("coding")
        if isinstance(coding, list):
            for item in coding:
                if not isinstance(item, dict):
                    continue
                display = item.get("display")
                if isinstance(display, str) and display.strip():
                    return display.strip()
    return None


def _tokenize(value: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def _tokens_from_terms(terms: Iterable[str]) -> List[str]:
    return [token for term in terms for token in _tokenize(term)]


def _fuzzy_matches(
    query_tokens: Iterable[str], event_tokens: Set[str]
) -> List[Tuple[str, str, float]]:
    matches: List[Tuple[str, str, float]] = []
    for query_token in query_tokens:
        if len(query_token) < 5 or query_token in _GENERIC_MEDICATION_TERMS:
            continue
        best_token = ""
        best_ratio = 0.0
        for event_token in event_tokens:
            if len(event_token) < 4:
                continue
            ratio = SequenceMatcher(None, query_token, event_token).ratio()
            if ratio > best_ratio:
                best_token = event_token
                best_ratio = ratio
        if best_ratio >= 0.78:
            matches.append((query_token, best_token, best_ratio))
    return sorted(matches, key=lambda item: item[2], reverse=True)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _event_timestamp(event: ClinicalEvent) -> float:
    parsed = _parse_time(event.event_time)
    return parsed.timestamp() if parsed else 0.0


def _rank_sort_key(item: RankedClinicalEvent) -> Tuple[float, float]:
    return item.score, _event_timestamp(item.event)
