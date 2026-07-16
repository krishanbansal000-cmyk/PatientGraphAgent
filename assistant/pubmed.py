"""Small, read-only PubMed E-utilities client for clinical evidence retrieval."""

from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any, Dict, List, Optional

import requests

from assistant.utils.cache import DEFAULT_TTL, get_cache
from assistant.utils.rate_limiter import get_rate_limiter
from assistant.utils.retry import RetryOptions, retry


_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_ARTICLE_TYPE_FILTERS = {
    "systematic review": "systematic review[Publication Type]",
    "meta-analysis": "meta-analysis[Publication Type]",
    "randomized controlled trial": "randomized controlled trial[Publication Type]",
    "clinical trial": "clinical trial[Publication Type]",
    "guideline": "guideline[Publication Type]",
    "review": "review[Publication Type]",
}
_MONTHS = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}


class PubMedClient:
    """Search PubMed and return compact, structured article metadata."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        tool: Optional[str] = None,
        email: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("NCBI_API_KEY", "")).strip()
        self.tool = (tool if tool is not None else os.getenv("NCBI_TOOL", "patientgraphagent")).strip()
        self.email = (email if email is not None else os.getenv("NCBI_EMAIL", "")).strip()
        self.timeout = timeout
        rate = 10.0 if self.api_key else 3.0
        self._rate_limiter = get_rate_limiter(f"pubmed_{int(rate)}", rate)
        self._cache = get_cache()

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        date_start: str = "",
        date_end: str = "",
        article_type: str = "",
    ) -> List[Dict[str, Any]]:
        """Search PubMed and fetch details for the matching PMIDs.

        The query must contain clinical concepts only. Callers must not include a
        patient name, identifier, or other patient-specific text.
        """
        normalized_query = " ".join(str(query or "").split())
        if not normalized_query:
            raise ValueError("PubMed query is required")
        if len(normalized_query) > 500:
            raise ValueError("PubMed query must be 500 characters or fewer")

        result_limit = max(1, min(int(max_results), 8))
        start = self._validate_date(date_start, "date_start")
        end = self._validate_date(date_end, "date_end")
        if start and end and start > end:
            raise ValueError("date_start must be on or before date_end")

        normalized_type = " ".join(str(article_type or "").lower().split())
        if normalized_type and normalized_type not in _ARTICLE_TYPE_FILTERS:
            allowed = ", ".join(sorted(_ARTICLE_TYPE_FILTERS))
            raise ValueError(f"Unsupported article_type. Use one of: {allowed}")

        cache_material = "|".join(
            (normalized_query.lower(), str(result_limit), start, end, normalized_type)
        )
        cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
        cached = self._cache.get("pubmed_search", cache_key)
        if cached is not None:
            return cached

        term = normalized_query
        if normalized_type:
            term = f"({term}) AND ({_ARTICLE_TYPE_FILTERS[normalized_type]})"

        params: Dict[str, Any] = {
            **self._common_params(),
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retmax": result_limit,
            "sort": "relevance",
        }
        if start or end:
            params["datetype"] = "pdat"
            if start:
                params["mindate"] = start.replace("-", "/")
            if end:
                params["maxdate"] = end.replace("-", "/")

        search_payload = self._request_json(_ESEARCH_URL, params)
        identifiers = [
            str(item).strip()
            for item in search_payload.get("esearchresult", {}).get("idlist", [])
            if str(item).strip()
        ]
        if not identifiers:
            self._cache.set("pubmed_search", cache_key, [], DEFAULT_TTL["SEARCH"])
            return []

        xml_text = self._request_text(
            _EFETCH_URL,
            {
                **self._common_params(),
                "db": "pubmed",
                "id": ",".join(identifiers),
                "retmode": "xml",
            },
        )
        articles = self.parse_articles(xml_text)
        by_pmid = {article["pmid"]: article for article in articles}
        ordered = [by_pmid[pmid] for pmid in identifiers if pmid in by_pmid]
        self._cache.set("pubmed_search", cache_key, ordered, DEFAULT_TTL["SEARCH"])
        return ordered

    def _common_params(self) -> Dict[str, str]:
        params = {"tool": self.tool or "patientgraphagent"}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _request_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        def request() -> Dict[str, Any]:
            self._rate_limiter.acquire_sync()
            response = requests.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()

        return retry(request, RetryOptions(max_retries=2))

    def _request_text(self, url: str, params: Dict[str, Any]) -> str:
        def request() -> str:
            self._rate_limiter.acquire_sync()
            response = requests.get(
                url,
                params=params,
                headers={"Accept": "application/xml"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.text

        return retry(request, RetryOptions(max_retries=2))

    @staticmethod
    def _validate_date(value: str, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        try:
            return date.fromisoformat(normalized).isoformat()
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)") from exc

    @classmethod
    def parse_articles(cls, xml_text: str) -> List[Dict[str, Any]]:
        """Parse PubMedArticle XML into a stable response contract."""
        root = ET.fromstring(xml_text)
        articles: List[Dict[str, Any]] = []
        for node in root.findall(".//PubmedArticle"):
            pmid = cls._text(node.find("./MedlineCitation/PMID"))
            if not pmid:
                continue
            article = node.find("./MedlineCitation/Article")
            if article is None:
                continue

            title = cls._text(article.find("./ArticleTitle")) or f"PubMed article {pmid}"
            abstract_parts = []
            for abstract in article.findall("./Abstract/AbstractText"):
                text = cls._text(abstract)
                if not text:
                    continue
                label = str(abstract.get("Label") or "").strip()
                abstract_parts.append(f"{label}: {text}" if label else text)

            authors = []
            for author in article.findall("./AuthorList/Author")[:8]:
                collective = cls._text(author.find("./CollectiveName"))
                name = collective or " ".join(
                    part
                    for part in (
                        cls._text(author.find("./ForeName")),
                        cls._text(author.find("./LastName")),
                    )
                    if part
                )
                if name:
                    authors.append(name)

            article_types = [
                cls._text(item)
                for item in article.findall("./PublicationTypeList/PublicationType")
                if cls._text(item)
            ][:8]
            mesh_terms = [
                cls._text(item)
                for item in node.findall("./MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName")
                if cls._text(item)
            ][:12]
            doi = ""
            for article_id in node.findall("./PubmedData/ArticleIdList/ArticleId"):
                if str(article_id.get("IdType") or "").lower() == "doi":
                    doi = cls._text(article_id)
                    break

            articles.append(
                {
                    "pmid": pmid,
                    "title": title,
                    "abstract": "\n".join(abstract_parts)[:3500],
                    "journal": cls._text(article.find("./Journal/Title")),
                    "published_at": cls._publication_date(article),
                    "authors": authors,
                    "article_types": article_types,
                    "mesh_terms": mesh_terms,
                    "doi": doi,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                }
            )
        return articles

    @staticmethod
    def _text(node: Optional[ET.Element]) -> str:
        if node is None:
            return ""
        return " ".join("".join(node.itertext()).split())

    @classmethod
    def _publication_date(cls, article: ET.Element) -> str:
        date_node = article.find("./ArticleDate")
        if date_node is None:
            date_node = article.find("./Journal/JournalIssue/PubDate")
        if date_node is None:
            return ""

        year = cls._text(date_node.find("./Year"))
        month = cls._text(date_node.find("./Month"))
        day = cls._text(date_node.find("./Day"))
        if not year:
            return cls._text(date_node.find("./MedlineDate"))
        normalized_month = _MONTHS.get(month[:3].lower(), month.zfill(2) if month.isdigit() else "")
        parts = [year]
        if normalized_month:
            parts.append(normalized_month)
        if day and normalized_month:
            parts.append(day.zfill(2))
        return "-".join(parts)
