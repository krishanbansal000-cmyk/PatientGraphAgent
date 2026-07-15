"""DailyMed API client for FDA drug labels.

Fetches Structured Product Labels (SPL) by RxCUI and extracts
patient-friendly sections: indications, side effects, warnings, how to take.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from assistant.utils.cache import get_cache, DEFAULT_TTL
from assistant.utils.rate_limiter import get_rate_limiter
from assistant.utils.retry import retry, RetryOptions


class DailyMedClient:
    """DailyMed REST API v2 client.

    Accesses FDA drug labels at dailymed.nlm.nih.gov/dailymed/services/v2/
    """

    BASE_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2"

    def __init__(self):
        self.cache = get_cache()
        self.rate_limiter = get_rate_limiter("nlm", 10.0)

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{path}"

        def _do():
            self.rate_limiter.acquire_sync()
            response = requests.get(url, params=params or {}, timeout=30, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()

        return retry(_do, RetryOptions(max_retries=2, retryable_status_codes=[408, 429, 500, 502, 503, 504]))

    def _request_text(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        url = f"{self.BASE_URL}{path}"

        def _do():
            self.rate_limiter.acquire_sync()
            response = requests.get(url, params=params or {}, timeout=30)
            response.raise_for_status()
            return response.text

        return retry(_do, RetryOptions(max_retries=2, retryable_status_codes=[408, 429, 500, 502, 503, 504]))

    def search_spls_by_rxcui(self, rxcui: str, page_size: int = 5) -> List[Dict[str, Any]]:
        """Search for SPL documents by RxCUI."""
        cache_key = f"spls:{rxcui}"

        def _fetch():
            data = self._request("/spls.json", {"rxcui": rxcui, "pagesize": page_size})
            return data.get("data", [])

        return self.cache.get_or_set("dailymed", cache_key, _fetch, DEFAULT_TTL["LOOKUP"])

    def search_spls_by_name(self, drug_name: str, page_size: int = 5) -> List[Dict[str, Any]]:
        """Search for SPL documents by drug name."""
        cache_key = f"spls_name:{drug_name.lower()}"

        def _fetch():
            data = self._request(
                "/spls.json",
                {
                    "drug_name": drug_name,
                    "name_type": "generic",
                    "pagesize": page_size,
                },
            )
            return data.get("data", [])

        return self.cache.get_or_set("dailymed", cache_key, _fetch, DEFAULT_TTL["SEARCH"])

    def get_spl_document(self, set_id: str) -> str:
        """Get the full SPL XML document by set ID."""
        cache_key = f"spl:{set_id}"

        def _fetch():
            return self._request_text(f"/spls/{set_id}.xml")

        return self.cache.get_or_set("dailymed", cache_key, _fetch, DEFAULT_TTL["STATIC"])

    def get_drug_info(self, rxcui: str, drug_name: str = "") -> Optional[Dict[str, Any]]:
        """Get patient-friendly drug information from DailyMed.

        Tries RxCUI search first, then falls back to drug name search.
        Many product-level RxCUIs don't have SPLs in DailyMed, so name
        fallback is essential.
        """
        cache_key = f"drug_info:{rxcui}:{drug_name}"

        def _fetch():
            # Ingredient-level RxCUIs can return mostly combination products.
            # Merge exact RxCUI and generic-name candidates, then rank the
            # combined set so a suitable single-drug label is not hidden.
            spls = self.search_spls_by_rxcui(rxcui, page_size=20) if rxcui else []
            if drug_name:
                by_set_id = {
                    str(spl.get("setid") or ""): spl
                    for spl in spls
                    if spl.get("setid")
                }
                for spl in self.search_spls_by_name(drug_name, page_size=20):
                    set_id = str(spl.get("setid") or "")
                    if set_id:
                        by_set_id.setdefault(set_id, spl)
                spls = list(by_set_id.values())

            if not spls:
                return None

            spl = self._select_spl(spls, drug_name)
            set_id = spl.get("setid")
            title = spl.get("title", "")

            if not set_id:
                return None

            # Fetch the full SPL XML and extract sections
            xml_text = self.get_spl_document(set_id)
            sections = self._extract_sections(xml_text)

            return {
                "rxcui": rxcui,
                "title": title,
                "set_id": set_id,
                "published_date": spl.get("published_date"),
                "selection_reason": "best exact single-drug DailyMed label match",
                "indications": sections.get("indications", ""),
                "warnings": sections.get("warnings", ""),
                "adverse_reactions": sections.get("adverse_reactions", ""),
                "dosage_and_administration": sections.get("dosage", ""),
                "drug_interactions": sections.get("drug_interactions", ""),
                "contraindications": sections.get("contraindications", ""),
                "patient_summary": self._build_patient_summary(title, sections),
            }

        return self.cache.get_or_set("dailymed", cache_key, _fetch, DEFAULT_TTL["LOOKUP"])

    @staticmethod
    def _select_spl(spls: List[Dict[str, Any]], drug_name: str) -> Dict[str, Any]:
        """Prefer the newest exact single-drug label over combination products."""
        requested = re.sub(r"[^a-z0-9]+", " ", drug_name.lower()).strip()
        combination_requested = " and " in f" {requested} "

        def published_value(value: Any) -> float:
            try:
                return datetime.strptime(str(value), "%b %d, %Y").timestamp()
            except (TypeError, ValueError):
                return 0.0

        def rank(spl: Dict[str, Any]) -> tuple[int, float, int]:
            raw_title = str(spl.get("title") or "")
            product_title = raw_title.split("[", 1)[0]
            normalized_title = re.sub(
                r"[^a-z0-9]+", " ", product_title.lower()
            ).strip()
            score = 0
            if requested and normalized_title.startswith(requested):
                score += 100
            elif requested and requested in normalized_title:
                score += 40
            is_combination = " and " in f" {normalized_title} "
            if is_combination and not combination_requested:
                score -= 100
            elif not is_combination and not combination_requested:
                score += 25
            try:
                version = int(spl.get("spl_version") or 0)
            except (TypeError, ValueError):
                version = 0
            return score, published_value(spl.get("published_date")), version

        return max(spls, key=rank)

    def _extract_sections(self, xml_text: str) -> Dict[str, str]:
        """Extract key sections from SPL XML document.

        SPL XML structure:
        <section>
          <code code="34067-9" displayName="INDICATIONS & USAGE SECTION"/>
          <text>actual content here</text>
        </section>
        """
        sections = {}

        # Map of SPL section codes to our keys
        section_map = {
            "34067-9": "indications",
            "43685-7": "warnings",
            "34084-4": "adverse_reactions",
            "34068-7": "dosage",
            "34073-7": "drug_interactions",
            "34070-3": "contraindications",
            "34069-5": "how_supplied",
            "34076-0": "patient_info",
            "34066-1": "boxed_warning",
        }

        # Find all <section>...</section> blocks
        section_pattern = re.compile(r"<section[^>]*>(.*?)</section>", re.DOTALL | re.IGNORECASE)
        for section_match in section_pattern.finditer(xml_text):
            section_content = section_match.group(1)

            # Find the code in this section
            code_match = re.search(r'<code\s+code="([^"]*)"', section_content)
            if not code_match:
                continue

            code = code_match.group(1)
            if code not in section_map:
                continue

            key = section_map[code]

            # Extract <text> content from this section
            text_match = re.search(r"<text[^>]*>(.*?)</text>", section_content, re.DOTALL | re.IGNORECASE)
            if text_match:
                text = text_match.group(1)
                # Remove nested XML tags, keep text
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 50:
                    sections[key] = text[:5000]

        return sections

    def _build_patient_summary(self, title: str, sections: Dict[str, str]) -> str:
        """Build a short patient-friendly summary from extracted sections."""
        parts = []
        if sections.get("indications"):
            parts.append(f"What it's used for: {sections['indications'][:500]}")
        if sections.get("adverse_reactions"):
            parts.append(f"Side effects: {sections['adverse_reactions'][:500]}")
        if sections.get("warnings"):
            parts.append(f"Warnings: {sections['warnings'][:500]}")
        if sections.get("dosage"):
            parts.append(f"How to take: {sections['dosage'][:500]}")
        return "\n\n".join(parts) if parts else "No detailed information available."
