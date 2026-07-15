"""Medical terminology resolver with real API clients.

Ported from medical-terminologies-mcp src/clients/rxnorm-client.ts and nlm-client.ts.
Each client follows the same pattern: rate limit, retry, cache, real API calls.
"""

from typing import Any, Dict, List, Optional

import requests

from assistant.utils.cache import get_cache, DEFAULT_TTL
from assistant.utils.rate_limiter import get_rate_limiter
from assistant.utils.retry import retry, RetryOptions


class RxNormClient:
    """RxNorm API client.

    Direct port of medical-terminologies-mcp/src/clients/rxnorm-client.ts.
    Uses https://rxnav.nlm.nih.gov/REST API.
    """

    BASE_URL = "https://rxnav.nlm.nih.gov/REST"

    def __init__(self):
        self.cache = get_cache()
        self.rate_limiter = get_rate_limiter("rxnorm", 20.0)

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{path}"

        def _do():
            self.rate_limiter.acquire_sync()
            response = requests.get(url, params=params, timeout=30, headers={"Accept": "application/json"})
            if response.status_code == 404:
                raise ValueError(f"RxNorm resource not found: {path}")
            response.raise_for_status()
            return response.json()

        return retry(_do, RetryOptions(max_retries=2, retryable_status_codes=[408, 429, 500, 502, 503, 504]))

    def get_approximate_match(self, term: str, max_results: int = 25) -> List[Dict[str, Any]]:
        """Get approximate matches for a drug name (getApproximateMatch)."""
        cache_key = f"approx:{term.lower()}:{max_results}"

        def _fetch():
            data = self._request("/approximateTerm.json", {"term": term, "maxEntries": max_results})
            candidates = (data.get("approximateGroup") or {}).get("candidate", [])
            return [
                {
                    "rxcui": c.get("rxcui"),
                    "rxaui": c.get("rxaui", ""),
                    "name": c.get("name", ""),
                    "score": int(float(c.get("score", 0))) if c.get("score") else 0,
                    "rank": int(float(c.get("rank", 0))) if c.get("rank") else 0,
                }
                for c in candidates
            ]

        return self.cache.get_or_set("rxnorm", cache_key, _fetch, DEFAULT_TTL["SEARCH"])

    def get_concept(self, rxcui: str) -> Optional[Dict[str, Any]]:
        """Get concept details by RxCUI (getConcept)."""
        cache_key = f"concept:{rxcui}"

        def _fetch():
            data = self._request(f"/rxcui/{rxcui}/properties.json")
            concept = (data.get("properties") or {})
            if not concept:
                return None
            return {
                "rxcui": concept.get("rxcui"),
                "name": concept.get("name"),
                "synonym": concept.get("synonym", ""),
                "tty": concept.get("tty"),
                "language": concept.get("language", "ENG"),
                "status": concept.get("status", "Unknown"),
            }

        return self.cache.get_or_set("rxnorm", cache_key, _fetch, DEFAULT_TTL["LOOKUP"])

    def get_ingredients(self, rxcui: str) -> List[Dict[str, Any]]:
        """Get ingredients for a drug by RxCUI.

        Uses the related concepts API with rela=has_ingredient.
        """
        cache_key = f"ingredients:{rxcui}"

        def _fetch():
            try:
                data = self._request(f"/rxcui/{rxcui}/related.json", {"rela": "has_ingredient"})
                ingredients = []
                for group in (data.get("relatedGroup") or {}).get("conceptGroup", []):
                    for prop in group.get("conceptProperties", []):
                        if prop.get("tty") == "IN":
                            ingredients.append({
                                "rxcui": prop.get("rxcui"),
                                "name": prop.get("name"),
                                "tty": prop.get("tty"),
                            })
                return ingredients
            except Exception:
                return []

        return self.cache.get_or_set("rxnorm", cache_key, _fetch, DEFAULT_TTL["LOOKUP"])

    def get_dosage_forms(self, rxcui: str) -> List[str]:
        """Get available dosage forms for a drug by RxCUI."""
        cache_key = f"dosage_forms:{rxcui}"

        def _fetch():
            try:
                data = self._request(f"/rxcui/{rxcui}/allProperties.json", {"prop": "attributes"})
                props = data.get("properties", {})
                # dosage form is in the "Dosage Form" attribute
                forms = []
                for attr in props.get("attribute", []):
                    if attr.get("propName", "").lower() in ("dosage form", "rxnorm_dose_form"):
                        forms.append(attr.get("propValue", ""))
                return list(set(forms))  # deduplicate
            except Exception:
                return []

        return self.cache.get_or_set("rxnorm", cache_key, _fetch, DEFAULT_TTL["LOOKUP"])

    def normalize_drug(self, name: str) -> Dict[str, Any]:
        """Full drug normalization: name → RxCUI, ingredients, dosage forms.

        This is the normalize_drug tool from the Phase 1 spec.
        """
        # Step 1: Find RxCUI via approximate match
        matches = self.get_approximate_match(name, max_results=5)
        if not matches:
            return {"query": name, "rxcui": None, "error": "No match found"}

        best = matches[0]
        rxcui = best.get("rxcui")
        if not rxcui:
            return {"query": name, "rxcui": None, "error": "No RxCUI found"}

        # Step 2: Get concept details
        concept = self.get_concept(rxcui)

        # Step 3: Get ingredients
        ingredients = self.get_ingredients(rxcui)

        # Step 4: Get dosage forms
        dosage_forms = self.get_dosage_forms(rxcui)

        # Use concept name, fall back to matched name
        canonical_name = (concept.get("name") if concept else None) or best.get("name") or name

        return {
            "query": name,
            "rxcui": rxcui,
            "canonical_name": canonical_name,
            "matched_name": best.get("name"),
            "match_score": best.get("score"),
            "ingredients": ingredients,
            "dosage_forms": dosage_forms,
        }


class LoincClient:
    """LOINC API client via NLM Clinical Tables.

    Direct port of medical-terminologies-mcp/src/clients/nlm-client.ts LOINC methods.
    Uses https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search.
    """

    BASE_URL = "https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search"
    DEFAULT_FIELDS = "LOINC_NUM,COMPONENT,PROPERTY,TIME_ASPCT,SYSTEM,SCALE_TYP,METHOD_TYP,CLASS,STATUS,SHORTNAME,COMMON_TEST_RANK,COMMON_ORDER_RANK"

    def __init__(self):
        self.cache = get_cache()
        self.rate_limiter = get_rate_limiter("nlm", 10.0)

    def _search(self, terms: str, scope_field: Optional[str] = None, max_list: int = 25) -> List[Dict[str, Any]]:
        params = {
            "terms": terms,
            "maxList": max_list,
            "df": self.DEFAULT_FIELDS,
        }
        if scope_field:
            params["sf"] = scope_field

        def _do():
            self.rate_limiter.acquire_sync()
            response = requests.get(self.BASE_URL, params=params, timeout=30)
            response.raise_for_status()
            return response.json()

        data = retry(_do, RetryOptions(max_retries=2, retryable_status_codes=[408, 429, 500, 502, 503, 504]))
        if not isinstance(data, list) or len(data) < 4:
            return []
        _, codes, _extra_fields, items, *_ = data
        if not codes or not items:
            return []
        # Clinical Tables returns extra fields requested with ``ef`` in item 3
        # and display rows requested with ``df`` in item 4. The prior parser
        # treated item 3 as a field-name list, but it is commonly null when no
        # ``ef`` parameter is supplied.
        field_list = self.DEFAULT_FIELDS.split(",")
        results = []
        for code, item in zip(codes, items):
            row = dict(zip(field_list, item))
            row["LOINC_NUM"] = str(row.get("LOINC_NUM") or code)
            results.append({
                "loinc_num": row.get("LOINC_NUM"),
                "component": row.get("COMPONENT"),
                "property": row.get("PROPERTY"),
                "time_aspect": row.get("TIME_ASPCT"),
                "system": row.get("SYSTEM"),
                "scale": row.get("SCALE_TYP"),
                "method": row.get("METHOD_TYP"),
                "class": row.get("CLASS"),
                "status": row.get("STATUS"),
                "shortname": row.get("SHORTNAME"),
            })
        return results

    def search_loinc(self, query: str, max_results: int = 25) -> List[Dict[str, Any]]:
        """Search LOINC by text query (searchLOINC)."""
        cache_key = f"search:{query.lower()}:{max_results}"
        return self.cache.get_or_set(
            "loinc", cache_key, lambda: self._search(query, max_list=max_results), DEFAULT_TTL["SEARCH"]
        )

    def get_loinc_details(self, loinc_num: str) -> Optional[Dict[str, Any]]:
        """Get exact LOINC details by code (getLOINCDetails).

        Uses sf=LOINC_NUM to scope to exact code field, maxList=10 to handle prefix codes.
        """
        cache_key = f"details:{loinc_num}"

        def _fetch():
            results = self._search(loinc_num, scope_field="LOINC_NUM", max_list=10)
            for r in results:
                if r.get("loinc_num") == loinc_num:
                    return r
            return results[0] if results else None

        return self.cache.get_or_set("loinc", cache_key, _fetch, DEFAULT_TTL["LOOKUP"])
