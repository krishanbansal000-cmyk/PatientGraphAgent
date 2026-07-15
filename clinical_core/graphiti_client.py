"""Optional Graphiti client configured for Vertex AI and the existing Aura graph."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional


log = logging.getLogger(__name__)

# Patient-derived graph metadata must not be included in Graphiti telemetry.
os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None, default: int) -> int:
    try:
        return max(1, int(str(value or default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class GraphitiSettings:
    enabled: bool
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str
    google_cloud_project: str
    google_cloud_location: str
    llm_model: str
    small_llm_model: str
    embedding_model: str
    embedding_dimension: int
    reranker_model: str
    max_coroutines: int

    @classmethod
    def from_environment(cls) -> "GraphitiSettings":
        return cls(
            enabled=_is_true(os.environ.get("MEDGRAPHITI_ENABLED")),
            neo4j_uri=os.environ.get("NEO4J_URI", "").strip(),
            neo4j_username=os.environ.get("NEO4J_USERNAME", "").strip(),
            neo4j_password=os.environ.get("NEO4J_PASSWORD", ""),
            neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j").strip() or "neo4j",
            google_cloud_project=os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app").strip(),
            google_cloud_location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1").strip(),
            llm_model=os.environ.get("MEDGRAPHITI_LLM_MODEL", "gemini-2.5-flash").strip(),
            small_llm_model=os.environ.get(
                "MEDGRAPHITI_SMALL_LLM_MODEL", "gemini-2.5-flash-lite"
            ).strip(),
            embedding_model=os.environ.get(
                "MEDGRAPHITI_EMBEDDING_MODEL", "text-embedding-005"
            ).strip(),
            embedding_dimension=max(
                1, int(os.environ.get("MEDGRAPHITI_EMBEDDING_DIMENSION", "768"))
            ),
            reranker_model=os.environ.get(
                "MEDGRAPHITI_RERANKER_MODEL", "gemini-2.5-flash-lite"
            ).strip(),
            max_coroutines=_positive_int(os.environ.get("MEDGRAPHITI_MAX_COROUTINES"), 2),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.neo4j_uri
            and self.neo4j_username
            and self.neo4j_password
            and self.google_cloud_project
            and self.google_cloud_location
        )


def graphiti_is_configured(settings: GraphitiSettings | None = None) -> bool:
    return (settings or GraphitiSettings.from_environment()).configured


def create_graphiti_client(settings: GraphitiSettings | None = None) -> Optional[Any]:
    """Construct a Graphiti client, or return ``None`` when the feature is disabled.

    Construction is synchronous; callers must later ``await client.close()``.
    No graph data is created, cleared, or modified by this function.
    """
    resolved = settings or GraphitiSettings.from_environment()
    if not resolved.configured:
        if resolved.enabled:
            log.warning("MedGraphiti is enabled but its Neo4j or Vertex configuration is incomplete")
        return None

    try:
        from google import genai
        from google.genai import types
        from graphiti_core import Graphiti
        from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient
        from graphiti_core.driver.neo4j_driver import Neo4jDriver
        from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
        from graphiti_core.llm_client import LLMConfig
        from graphiti_core.llm_client.gemini_client import GeminiClient
    except ImportError:
        log.exception("MedGraphiti dependencies are unavailable")
        return None

    vertex_client = genai.Client(
        vertexai=True,
        project=resolved.google_cloud_project,
        location=resolved.google_cloud_location,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                attempts=4,
                initial_delay=1.0,
                max_delay=8.0,
                exp_base=2.0,
                jitter=1.0,
                http_status_codes=[429, 500, 502, 503, 504],
            )
        ),
    )
    llm_config = LLMConfig(
        model=resolved.llm_model,
        small_model=resolved.small_llm_model,
        temperature=0.0,
    )
    reranker_config = LLMConfig(model=resolved.reranker_model, temperature=0.0)
    driver = Neo4jDriver(
        resolved.neo4j_uri,
        resolved.neo4j_username,
        resolved.neo4j_password,
        database=resolved.neo4j_database,
    )
    return Graphiti(
        graph_driver=driver,
        llm_client=GeminiClient(config=llm_config, client=vertex_client),
        embedder=GeminiEmbedder(
            config=GeminiEmbedderConfig(
                embedding_model=resolved.embedding_model,
                embedding_dim=resolved.embedding_dimension,
            ),
            client=vertex_client,
        ),
        cross_encoder=GeminiRerankerClient(config=reranker_config, client=vertex_client),
        store_raw_episode_content=False,
        max_coroutines=resolved.max_coroutines,
    )
