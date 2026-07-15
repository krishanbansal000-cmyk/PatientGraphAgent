"""Extract safe, structured citations from ADK tool response events."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse


_ALLOWED_SOURCE_TYPES = {
    "patient_record",
    "drug_label",
    "terminology",
    "interaction_database",
}
_ALLOWED_URL_HOSTS = {
    "clinicaltables.nlm.nih.gov",
    "dailymed.nlm.nih.gov",
    "ftp.cdc.gov",
    "rxnav.nlm.nih.gov",
    "www.nlm.nih.gov",
}
_PUBLIC_FIELDS = {
    "id",
    "type",
    "title",
    "publisher",
    "resource_type",
    "resource_id",
    "status",
    "date",
    "set_id",
    "url",
    "code",
    "severity",
    "ddinter_id_a",
    "ddinter_id_b",
    "dataset",
    "version",
}


def collect_event_citations(events: Iterable[Any]) -> List[Dict[str, Any]]:
    """Collect citations only from function responses emitted by the ADK run."""
    citations: Dict[str, Dict[str, Any]] = {}

    for event in events:
        get_responses = getattr(event, "get_function_responses", None)
        if not callable(get_responses):
            continue
        for function_response in get_responses() or []:
            tool_name = str(getattr(function_response, "name", "") or "")
            response = getattr(function_response, "response", None)
            for candidate in _find_sources(response):
                source = _sanitize_source(candidate)
                if not source:
                    continue
                source_id = source["id"]
                if source_id not in citations:
                    source["tools"] = []
                    citations[source_id] = source
                if tool_name and tool_name not in citations[source_id]["tools"]:
                    citations[source_id]["tools"].append(tool_name)

    result = list(citations.values())
    for number, source in enumerate(result, start=1):
        source["number"] = number
    return result


def _find_sources(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        sources = value.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    yield source
        for key in ("result", "data"):
            if key in value:
                yield from _find_sources(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _find_sources(item)


def _sanitize_source(source: Dict[str, Any]) -> Dict[str, Any]:
    source_id = str(source.get("id") or "").strip()
    source_type = str(source.get("type") or "").strip()
    title = str(source.get("title") or "").strip()
    if not source_id or source_type not in _ALLOWED_SOURCE_TYPES or not title:
        return {}

    clean = {
        key: value
        for key, value in source.items()
        if key in _PUBLIC_FIELDS and value not in (None, "", [])
    }
    if "url" in clean and not _allowed_url(str(clean["url"])):
        clean.pop("url")
    clean["id"] = source_id
    clean["type"] = source_type
    clean["title"] = title
    return clean


def _allowed_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.hostname in _ALLOWED_URL_HOSTS
