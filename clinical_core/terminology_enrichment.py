"""Deterministic terminology enrichment for patient-owned FHIR evidence.

The FHIR store remains authoritative for the patient's facts. This module only
validates and labels codes already present in retrieved FHIR resources. It does
not infer diagnoses, translate between vocabularies, or load whole vocabularies
into Neo4j.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from google.cloud import bigquery


log = logging.getLogger(__name__)

_TABLE_ID = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_CODEABLE_FIELDS = (
    "code",
    "medicationCodeableConcept",
    "valueCodeableConcept",
    "vaccineCode",
)

_VOCABULARY_META: Dict[str, Dict[str, str]] = {
    "RxNorm": {
        "publisher": "RxNorm / U.S. National Library of Medicine",
        "source_url": "https://www.nlm.nih.gov/research/umls/rxnorm/",
    },
    "ICD-10-CM": {
        "publisher": "CDC National Center for Health Statistics",
        "source_url": (
            "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/"
            "ICD10CM/2026-update/"
        ),
    },
    "SNOMED CT": {
        "publisher": "SNOMED International",
        "source_url": "https://www.snomed.org/get-snomed",
    },
    "LOINC": {
        "publisher": "Regenstrief Institute",
        "source_url": "https://loinc.org/",
    },
}


@dataclass(frozen=True)
class TerminologyConfig:
    """Version-pinned terminology sources used by the POC."""

    enabled: bool
    location: str
    rxnorm_table: str
    rxnorm_version: str
    icd10_table: str
    icd10_version: str

    @classmethod
    def from_environment(cls) -> "TerminologyConfig":
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app")
        rxnorm_release = os.environ.get(
            "TERMINOLOGY_RXNORM_RELEASE", "07_26"
        ).strip()
        return cls(
            enabled=os.environ.get("TERMINOLOGY_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            location=os.environ.get("TERMINOLOGY_BIGQUERY_LOCATION", "US").strip()
            or "US",
            # The public `rxnconso_current` alias is stale (2022). Pin a known
            # monthly release so results remain auditable and reproducible.
            rxnorm_table=os.environ.get(
                "TERMINOLOGY_RXNORM_TABLE",
                f"bigquery-public-data.nlm_rxnorm.rxnconso_{rxnorm_release}",
            ).strip(),
            rxnorm_version=os.environ.get(
                "TERMINOLOGY_RXNORM_VERSION", "2026-07-06"
            ).strip(),
            icd10_table=os.environ.get(
                "TERMINOLOGY_ICD10_TABLE",
                f"{project}.medical_terminology.icd10cm_2026",
            ).strip(),
            icd10_version=os.environ.get(
                "TERMINOLOGY_ICD10_VERSION", "2026-04-01"
            ).strip(),
        )


def _vocabulary(system: str) -> Optional[str]:
    normalized = system.strip().lower().rstrip("/")
    oid_map = {
        "urn:oid:2.16.840.1.113883.6.88": "RxNorm",
        "urn:oid:2.16.840.1.113883.6.90": "ICD-10-CM",
        "urn:oid:2.16.840.1.113883.6.96": "SNOMED CT",
        "urn:oid:2.16.840.1.113883.6.1": "LOINC",
    }
    if normalized in oid_map:
        return oid_map[normalized]
    if "rxnorm" in normalized:
        return "RxNorm"
    if "snomed" in normalized:
        return "SNOMED CT"
    if "loinc.org" in normalized:
        return "LOINC"
    if "icd-10-cm" in normalized or "icd10cm" in normalized:
        return "ICD-10-CM"
    return None


def collect_fhir_codings(
    resources: Sequence[Dict[str, Any]],
    *,
    fhir_keys: Sequence[str] = (),
    max_concepts: int = 80,
) -> List[Dict[str, Any]]:
    """Collect terminology codes from selected patient FHIR resources."""
    selected = {value for value in fhir_keys if value}
    concepts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for resource in resources:
        resource_type = str(resource.get("resourceType") or "").strip()
        resource_id = str(resource.get("id") or "").strip()
        if not resource_type or not resource_id:
            continue
        fhir_key = f"{resource_type}/{resource_id}"
        if selected and fhir_key not in selected:
            continue

        for field in _CODEABLE_FIELDS:
            codeable = resource.get(field)
            if not isinstance(codeable, dict):
                continue
            text = str(codeable.get("text") or "").strip()
            for coding in codeable.get("coding", []):
                if not isinstance(coding, dict):
                    continue
                system = str(coding.get("system") or "").strip()
                code = str(coding.get("code") or "").strip()
                vocabulary = _vocabulary(system)
                if not vocabulary or not code:
                    continue
                key = (system, code)
                row = concepts.setdefault(
                    key,
                    {
                        "key": f"{system}|{code}",
                        "system": system,
                        "vocabulary": vocabulary,
                        "code": code,
                        "display": str(coding.get("display") or text or code),
                        "canonical_display": None,
                        "version": None,
                        "validation_status": "source_only",
                        "match_method": "FHIR coding",
                        "fhir_sources": [],
                        **_VOCABULARY_META[vocabulary],
                    },
                )
                if fhir_key not in row["fhir_sources"]:
                    row["fhir_sources"].append(fhir_key)
                if len(concepts) >= max(1, min(max_concepts, 200)):
                    break
            if len(concepts) >= max(1, min(max_concepts, 200)):
                break
        if len(concepts) >= max(1, min(max_concepts, 200)):
            break
    return list(concepts.values())


class TerminologyEnricher:
    """Batch lookup facade over versioned BigQuery terminology tables."""

    def __init__(
        self,
        client: Optional[bigquery.Client] = None,
        config: Optional[TerminologyConfig] = None,
    ):
        self.config = config or TerminologyConfig.from_environment()
        self._client = client

    def _get_client(self) -> bigquery.Client:
        if self._client is None:
            project = os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app")
            self._client = bigquery.Client(project=project, location=self.config.location)
        return self._client

    @staticmethod
    def _safe_table(table: str) -> str:
        if not _TABLE_ID.fullmatch(table):
            raise ValueError("Terminology table must be a fully qualified BigQuery table")
        return table

    def enrich(
        self,
        resources: Sequence[Dict[str, Any]],
        *,
        fhir_keys: Sequence[str] = (),
        max_concepts: int = 80,
    ) -> Dict[str, Any]:
        concepts = collect_fhir_codings(
            resources,
            fhir_keys=fhir_keys,
            max_concepts=max_concepts,
        )
        response: Dict[str, Any] = {
            "enabled": self.config.enabled,
            "concepts": concepts,
            "datasets": [],
            "warnings": [],
        }
        if not self.config.enabled or not concepts:
            return response

        by_vocabulary: Dict[str, List[Dict[str, Any]]] = {}
        for concept in concepts:
            by_vocabulary.setdefault(concept["vocabulary"], []).append(concept)

        lookups = (
            ("RxNorm", self._lookup_rxnorm),
            ("ICD-10-CM", self._lookup_icd10),
        )
        for vocabulary, lookup in lookups:
            candidates = by_vocabulary.get(vocabulary, [])
            if not candidates:
                continue
            try:
                matches, dataset = lookup([row["code"] for row in candidates])
                if dataset:
                    response["datasets"].append(dataset)
                for concept in candidates:
                    match = matches.get(self._normalized_code(vocabulary, concept["code"]))
                    if match:
                        concept.update(match)
                        concept["validation_status"] = "validated"
            except Exception as exc:
                log.warning("%s terminology lookup unavailable: %s", vocabulary, type(exc).__name__)
                response["warnings"].append(
                    f"{vocabulary} enrichment unavailable ({type(exc).__name__}); "
                    "the original FHIR coding was retained."
                )
        return response

    @staticmethod
    def _normalized_code(vocabulary: str, code: str) -> str:
        if vocabulary == "ICD-10-CM":
            return re.sub(r"[^A-Za-z0-9]", "", code).upper()
        return code.strip()

    def _rows(self, sql: str, codes: Iterable[str]) -> List[Dict[str, Any]]:
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("codes", "STRING", list(dict.fromkeys(codes)))
            ]
        )
        result = self._get_client().query(
            sql,
            job_config=job_config,
            location=self.config.location,
        ).result()
        return [dict(row) for row in result]

    def _lookup_rxnorm(
        self, codes: Sequence[str]
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        table = self._safe_table(self.config.rxnorm_table)
        sql = f"""
            SELECT rxcui AS code,
                   ARRAY_AGG(
                     STRUCT(str AS display, tty AS term_type)
                     ORDER BY CASE WHEN ispref = 'Y' THEN 0 ELSE 1 END,
                              CASE tty WHEN 'SCD' THEN 0 WHEN 'SBD' THEN 1
                                       WHEN 'IN' THEN 2 ELSE 9 END,
                              str
                     LIMIT 1
                   )[OFFSET(0)] AS preferred
            FROM `{table}`
            WHERE rxcui IN UNNEST(@codes)
              AND sab = 'RXNORM' AND suppress = 'N' AND lat = 'ENG'
            GROUP BY rxcui
        """
        matches: Dict[str, Dict[str, Any]] = {}
        for row in self._rows(sql, codes):
            preferred = row.get("preferred") or {}
            if hasattr(preferred, "items"):
                preferred = dict(preferred)
            matches[str(row["code"])] = {
                "canonical_display": preferred.get("display"),
                "term_type": preferred.get("term_type"),
                "version": self.config.rxnorm_version,
                "dataset": table,
                "match_method": "exact RxCUI",
            }
        return matches, {
            "id": table,
            "vocabulary": "RxNorm",
            "version": self.config.rxnorm_version,
            "publisher": _VOCABULARY_META["RxNorm"]["publisher"],
            "url": _VOCABULARY_META["RxNorm"]["source_url"],
        }

    def _lookup_icd10(
        self, codes: Sequence[str]
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        table = self._safe_table(self.config.icd10_table)
        normalized = [self._normalized_code("ICD-10-CM", code) for code in codes]
        sql = f"""
            SELECT normalized_code AS code, description, version,
                   effective_date, is_billable, source_url
            FROM `{table}`
            WHERE normalized_code IN UNNEST(@codes)
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY normalized_code ORDER BY effective_date DESC
            ) = 1
        """
        matches = {
            str(row["code"]): {
                "canonical_display": row.get("description"),
                "version": str(row.get("version") or self.config.icd10_version),
                "effective_date": str(row.get("effective_date") or ""),
                "is_billable": row.get("is_billable"),
                "source_url": row.get("source_url")
                or _VOCABULARY_META["ICD-10-CM"]["source_url"],
                "dataset": table,
                "match_method": "exact ICD-10-CM code",
            }
            for row in self._rows(sql, normalized)
        }
        return matches, {
            "id": table,
            "vocabulary": "ICD-10-CM",
            "version": self.config.icd10_version,
            "publisher": _VOCABULARY_META["ICD-10-CM"]["publisher"],
            "url": _VOCABULARY_META["ICD-10-CM"]["source_url"],
        }

_enricher: Optional[TerminologyEnricher] = None


def get_terminology_enricher() -> TerminologyEnricher:
    global _enricher
    if _enricher is None:
        _enricher = TerminologyEnricher()
    return _enricher


def terminology_sources(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert enrichment datasets into the app's structured citation shape."""
    sources = []
    for dataset in context.get("datasets", []):
        dataset_id = str(dataset.get("id") or "").strip()
        if not dataset_id:
            continue
        sources.append(
            {
                "id": f"terminology:{dataset_id}:{dataset.get('version') or ''}",
                "type": "terminology",
                "title": f"{dataset.get('vocabulary')} terminology {dataset.get('version') or ''}".strip(),
                "publisher": dataset.get("publisher"),
                "dataset": dataset_id,
                "version": dataset.get("version"),
                "url": dataset.get("url"),
            }
        )
    return sources
