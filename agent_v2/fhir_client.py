"""FHIR client for Google Cloud Healthcare API.

Direct, simple HTTP calls with retry and rate limiting.
No async, no SQLite, no caching — just fetch from FHIR.
"""

import os
from typing import Any, Dict, List, Optional

from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession

from agent_v2.utils.rate_limiter import get_rate_limiter
from agent_v2.utils.retry import retry, RetryOptions


_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
_DATASET_ID = os.environ.get("HEALTHCARE_DATASET", "myhealth-dataset")
_FHIR_STORE_ID = os.environ.get("HEALTHCARE_FHIR_STORE", "myhealth-fhir-store")


class FhirClient:
    """Simple client for Google Cloud Healthcare API FHIR store."""

    def __init__(self):
        self.project_id = _PROJECT_ID
        self.location = _LOCATION
        self.dataset_id = _DATASET_ID
        self.fhir_store_id = _FHIR_STORE_ID
        self._session: Optional[AuthorizedSession] = None
        self._rate_limiter = get_rate_limiter("healthcare", 100.0)

    def _get_session(self) -> AuthorizedSession:
        if self._session is None:
            creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self._session = AuthorizedSession(creds)
        return self._session

    def _base_url(self) -> str:
        return (
            f"https://healthcare.googleapis.com/v1/projects/{self.project_id}"
            f"/locations/{self.location}/datasets/{self.dataset_id}"
            f"/fhirStores/{self.fhir_store_id}/fhir"
        )

    def _fetch(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Single HTTP GET with rate limiting and retry."""
        self._rate_limiter.acquire_sync()

        def _do():
            session = self._get_session()
            response = session.get(
                url, headers={"Accept": "application/fhir+json"}, params=params or {}, timeout=30
            )
            if response.status_code != 200:
                raise RuntimeError(f"FHIR API {response.status_code}: {response.text[:300]}")
            return response.json()

        return retry(_do, RetryOptions(max_retries=3))

    def get_resource(self, resource_type: str, resource_id: str) -> Dict[str, Any]:
        """Read a single FHIR resource by type and ID."""
        return self._fetch(f"{self._base_url()}/{resource_type}/{resource_id}")

    def search_resources(self, resource_type: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Search FHIR resources. Returns list of resources from the bundle."""
        bundle = self._fetch(f"{self._base_url()}/{resource_type}", params)
        return [
            entry["resource"]
            for entry in bundle.get("entry", [])
            if isinstance(entry, dict) and "resource" in entry
        ]

    def patient_everything(self, patient_id: str) -> List[Dict[str, Any]]:
        """Get all resources for a patient via $everything, following all pages."""
        url = f"{self._base_url()}/Patient/{patient_id}/$everything"
        resources: List[Dict[str, Any]] = []
        seen_urls: set = set()

        while url and len(seen_urls) < 1000:
            if url in seen_urls:
                break
            seen_urls.add(url)

            bundle = self._fetch(url)
            resources.extend(
                entry["resource"]
                for entry in bundle.get("entry", [])
                if isinstance(entry, dict) and "resource" in entry
            )

            # Follow next link
            url = None
            for link in bundle.get("link", []):
                if isinstance(link, dict) and link.get("relation") == "next":
                    url = link.get("url")
                    break

        # Also fetch referenced Practitioners/Organizations not in patient compartment
        return self._fetch_referenced(resources)

    def _fetch_referenced(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fetch Practitioner/Organization resources referenced by patient resources."""
        external_types = {"Practitioner", "Organization", "Location"}
        existing = {(r.get("resourceType"), r.get("id")) for r in resources if r.get("id")}
        to_fetch: Dict[str, set] = {t: set() for t in external_types}

        def scan(obj):
            if isinstance(obj, dict):
                ref = obj.get("reference", "")
                if isinstance(ref, str) and "/" in ref:
                    rtype, rid = ref.split("/", 1)
                    if rtype in external_types and rid:
                        to_fetch[rtype].add(rid)
                for v in obj.values():
                    scan(v)
            elif isinstance(obj, list):
                for item in obj:
                    scan(item)

        for r in resources:
            scan(r)

        for rtype, ids in to_fetch.items():
            for rid in ids:
                if (rtype, rid) not in existing:
                    try:
                        resources.append(self.get_resource(rtype, rid))
                    except Exception:
                        pass

        return resources
