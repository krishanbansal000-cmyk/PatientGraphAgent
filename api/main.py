"""
FastAPI API for the MyHealth ADK agent.
Single container deployment on Cloud Run.
"""

import logging
import os
import uuid
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dotenv import load_dotenv

# Load environment variables before importing the agent
load_dotenv()

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession
from google.genai import types

from agent_v2.agent import root_agent
from agent.patient_memory import get_patient_memory_service
from agent.patient_journey import build_patient_journey
from agent.tools import load_patient_resources
from api.citations import collect_event_citations
from api.prototype_auth import (
    COOKIE_NAME,
    PrototypeAuthConfig,
)

app = FastAPI(title="MyHealth Agent API")
log = logging.getLogger(__name__)

_AUTH_EXEMPT_PATHS = {"/api/auth/login", "/api/auth/status"}
_prototype_auth_config = PrototypeAuthConfig()


def _get_prototype_auth_config() -> PrototypeAuthConfig:
    return _prototype_auth_config


@app.middleware("http")
async def require_prototype_authentication(request: Request, call_next):
    """Require the simple prototype cookie for every API route."""
    path = request.url.path
    if not path.startswith("/api/") or path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)

    config = _get_prototype_auth_config()

    token = request.cookies.get(COOKIE_NAME, "")
    claims = config.verify_session_token(token) if token else None
    if claims is None:
        response = JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    request.state.prototype_user = claims["sub"]
    return await call_next(request)

# Serve the UI static files at /ui with no-cache headers
class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that sends no-cache headers to prevent stale UI."""
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

app.mount("/ui", NoCacheStaticFiles(directory="ui", html=True), name="ui")

# ADK runner setup
session_service = InMemorySessionService()
runner = Runner(
    app_name="myhealth_app",
    agent=root_agent,
    session_service=session_service,
)

# FHIR store configuration
_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
_DATASET_ID = os.environ.get("HEALTHCARE_DATASET", "myhealth-dataset")
_FHIR_STORE_ID = os.environ.get("HEALTHCARE_FHIR_STORE", "myhealth-fhir-store")
_FHIR_BASE_URL = (
    f"https://healthcare.googleapis.com/v1/projects/{_PROJECT_ID}/locations/{_LOCATION}"
    f"/datasets/{_DATASET_ID}/fhirStores/{_FHIR_STORE_ID}/fhir"
)

_authorized_session: Optional[AuthorizedSession] = None


def _get_fhir_session() -> AuthorizedSession:
    global _authorized_session
    if _authorized_session is None:
        credentials, _ = google_auth_default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        _authorized_session = AuthorizedSession(credentials)
    return _authorized_session


def _fhir_get(resource_type: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    session = _get_fhir_session()
    url = f"{_FHIR_BASE_URL}/{resource_type}"
    headers = {"Accept": "application/fhir+json"}
    response = session.get(url, headers=headers, params=params)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"FHIR API error: {response.status_code} {response.text}")
    return response.json()


def _fhir_get_by_id(resource_type: str, resource_id: str) -> Dict[str, Any]:
    session = _get_fhir_session()
    url = f"{_FHIR_BASE_URL}/{resource_type}/{resource_id}"
    headers = {"Accept": "application/fhir+json"}
    response = session.get(url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"FHIR API error: {response.status_code} {response.text}")
    return response.json()


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    patient_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = "default_user"
    episode_resource_ids: list[str] = Field(default_factory=list, max_length=20)


@app.get("/")
def health():
    return {"status": "ok", "service": "myhealth-agent"}


@app.get("/api/auth/status")
def auth_status(request: Request):
    config = _get_prototype_auth_config()
    token = request.cookies.get(COOKIE_NAME, "")
    return {
        "configured": True,
        "authenticated": bool(token and config.verify_session_token(token)),
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request):
    config = _get_prototype_auth_config()
    client_ip = request.client.host or "unknown"

    if config.is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")

    authenticated = config.password_matches(payload.password)
    config.record_login_attempt(client_ip, authenticated)

    if not authenticated:
        raise HTTPException(status_code=401, detail="Invalid password")

    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=config.create_session_token(),
        max_age=config.session_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/fhir/{resource_type}/{resource_id}")
def get_fhir_resource(resource_type: str, resource_id: str):
    """Return a full FHIR resource from the Healthcare FHIR store."""
    try:
        resource = _fhir_get_by_id(resource_type, resource_id)
        return resource
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fhir/{resource_type}/{resource_id}/related")
def get_fhir_resource_related(resource_type: str, resource_id: str):
    """Return related FHIR resources referenced by the given resource."""
    try:
        resource = _fhir_get_by_id(resource_type, resource_id)
        related = []

        def collect_references(obj, path=""):
            if isinstance(obj, dict):
                if "reference" in obj and isinstance(obj["reference"], str):
                    ref = obj["reference"]
                    if ref.startswith("http://") or ref.startswith("https://"):
                        return
                    parts = ref.split("/")
                    if len(parts) == 2:
                        rel_type, rel_id = parts
                        related.append({"type": rel_type, "id": rel_id, "reference": ref, "path": path})
                    return
                for key, value in obj.items():
                    collect_references(value, f"{path}.{key}" if path else key)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    collect_references(item, f"{path}[{i}]")

        collect_references(resource)

        # Deduplicate and fetch full resources for a limited set
        seen = set()
        unique_related = []
        for r in related:
            key = (r["type"], r["id"])
            if key not in seen:
                seen.add(key)
                unique_related.append(r)

        # Fetch full resource for each related reference (limit to 20 to avoid overload)
        fetched = []
        for r in unique_related[:20]:
            try:
                full = _fhir_get_by_id(r["type"], r["id"])
                fetched.append({"reference": r, "resource": full})
            except Exception:
                fetched.append({"reference": r, "resource": None, "error": "Unable to fetch resource"})

        return {"source": resource, "related": fetched}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/patient/{patient_id}/details")
def get_patient_details(patient_id: str):
    """Return structured patient details from the FHIR store."""
    try:
        patient = _fhir_get_by_id("Patient", patient_id)

        conditions_bundle = _fhir_get(
            "Condition", {"patient": f"Patient/{patient_id}", "_count": "100"}
        )
        conditions = []
        for entry in conditions_bundle.get("entry", []):
            resource = entry.get("resource", {})
            coding_list = resource.get("code", {}).get("coding", [])
            coding = coding_list[0] if coding_list else {}
            clinical_status = resource.get("clinicalStatus", {})
            status_text = ""
            if isinstance(clinical_status, dict):
                status_coding = clinical_status.get("coding", [])
                if status_coding:
                    status_text = status_coding[0].get("code", "")
            elif isinstance(clinical_status, str):
                status_text = clinical_status
            conditions.append(
                {
                    "id": resource.get("id"),
                    "condition_name": resource.get("code", {}).get("text"),
                    "code": coding.get("code"),
                    "system": coding.get("system"),
                    "clinical_status": status_text,
                    "resource_type": "Condition",
                }
            )

        medications_bundle = _fhir_get(
            "MedicationRequest", {"patient": f"Patient/{patient_id}", "_count": "100"}
        )
        medications = []
        for entry in medications_bundle.get("entry", []):
            resource = entry.get("resource", {})
            med_cc = resource.get("medicationCodeableConcept", {})
            coding_list = med_cc.get("coding", [])
            coding = coding_list[0] if coding_list else {}
            medications.append(
                {
                    "id": resource.get("id"),
                    "medication_name": med_cc.get("text"),
                    "code": coding.get("code"),
                    "system": coding.get("system"),
                    "status": resource.get("status"),
                    "resource_type": "MedicationRequest",
                }
            )

        observations_bundle = _fhir_get(
            "Observation",
            {"patient": f"Patient/{patient_id}", "_count": "10", "_sort": "-date"},
        )
        observations = []
        for entry in observations_bundle.get("entry", []):
            resource = entry.get("resource", {})
            coding_list = resource.get("code", {}).get("coding", [])
            coding = coding_list[0] if coding_list else {}
            value_q = resource.get("valueQuantity", {})
            observations.append(
                {
                    "id": resource.get("id"),
                    "observation_name": resource.get("code", {}).get("text"),
                    "loinc_code": coding.get("code"),
                    "loinc_system": coding.get("system"),
                    "value": value_q.get("value"),
                    "unit": value_q.get("unit"),
                    "date": resource.get("effectiveDateTime"),
                    "resource_type": "Observation",
                }
            )

        encounters_bundle = _fhir_get(
            "Encounter",
            {"patient": f"Patient/{patient_id}", "_count": "100", "_sort": "-date"},
        )
        encounters = []
        for entry in encounters_bundle.get("entry", []):
            resource = entry.get("resource", {})
            participants = []
            for participant in resource.get("participant", []):
                individual = participant.get("individual", {})
                if individual.get("reference"):
                    participants.append(individual.get("reference"))
            class_obj = resource.get("class", {})
            service_provider = resource.get("serviceProvider", {})
            encounters.append(
                {
                    "id": resource.get("id"),
                    "status": resource.get("status"),
                    "class_code": class_obj.get("code"),
                    "class_display": class_obj.get("display"),
                    "start": resource.get("period", {}).get("start"),
                    "end": resource.get("period", {}).get("end"),
                    "reason": resource.get("reasonCode", [{}])[0].get("text", ""),
                    "participants": participants,
                    "service_provider": service_provider.get("reference"),
                    "resource_type": "Encounter",
                }
            )

        return {
            "patient_id": patient_id,
            "patient": {
                "id": patient.get("id"),
                "gender": patient.get("gender"),
                "birthDate": patient.get("birthDate"),
            },
            "conditions": conditions,
            "medications": medications,
            "recent_observations": observations,
            "encounters": encounters,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/patient/{patient_id}/journey")
def get_patient_journey(
    patient_id: str,
    date_start: Optional[str] = Query(default=None, alias="from"),
    date_end: Optional[str] = Query(default=None, alias="to"),
    episode_type: Optional[str] = Query(default=None, alias="type"),
):
    """Return a deterministic, source-linked patient journey projection."""
    try:
        resources, source = load_patient_resources(patient_id)
        requested_types = episode_type.split(",") if episode_type else []
        journey = build_patient_journey(
            patient_id,
            resources,
            source=source,
            date_start=date_start,
            date_end=date_end,
            episode_types=requested_types,
        )
        memory = get_patient_memory_service().status(patient_id)
        payload = journey.model_dump(mode="json")
        payload["memory"] = memory
        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/internal/patient/{patient_id}/memory/status")
def get_patient_memory_status(patient_id: str):
    """Return this process's best-effort patient memory synchronization state."""
    return get_patient_memory_service().status(patient_id)


@app.post("/api/internal/patient/{patient_id}/memory/rebuild")
def rebuild_patient_memory(patient_id: str):
    """Explicitly rebuild Graphiti memory and provenance before returning."""
    try:
        resources, source = load_patient_resources(patient_id)
        return get_patient_memory_service().sync_now(
            patient_id,
            resources,
            source=source,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/agent/ask")
async def ask_agent(request: AskRequest):
    """Ask the agent a question about the active patient."""
    if not request.question:
        raise HTTPException(status_code=400, detail="question is required")

    patient_id = request.patient_id or os.environ.get("DEFAULT_PATIENT_ID", "")
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    user_id = request.user_id or "default_user"
    session_id = request.session_id or str(uuid.uuid4())
    session = await session_service.get_session(
        app_name="myhealth_app",
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        session = await session_service.create_session(
            app_name="myhealth_app",
            user_id=user_id,
            session_id=session_id,
            state={"patient_id": patient_id},
        )
    elif session.state.get("patient_id") != patient_id:
        raise HTTPException(status_code=409, detail="This chat session belongs to a different patient.")

    episode_resource_ids = [
        reference
        for reference in request.episode_resource_ids
        if isinstance(reference, str)
        and len(reference) <= 200
        and reference.count("/") == 1
    ]
    if episode_resource_ids:
        session.state["last_patient_resource_ids"] = episode_resource_ids

    content = types.Content(role="user", parts=[types.Part(text=request.question)])

    try:
        final_response = ""
        invocation_events = []
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            invocation_events.append(event)
            if event.is_final_response() and event.content and event.content.parts:
                event_response = "".join(part.text or "" for part in event.content.parts)
                if event_response:
                    final_response = event_response

        if not final_response:
            raise RuntimeError("Agent run completed without a final response")

        citations = collect_event_citations(invocation_events)
        return {
            "question": request.question,
            "patient_id": patient_id,
            "session_id": session_id,
            "answer": final_response,
            "citations": citations,
            "grounded": bool(citations),
        }
    except HTTPException:
        raise
    except Exception as exc:
        message = str(exc).upper()
        if "429" in message or "RESOURCE_EXHAUSTED" in message:
            log.warning("Vertex AI quota temporarily exhausted")
            raise HTTPException(
                status_code=503,
                detail="The health assistant is temporarily busy. Please retry shortly.",
            ) from exc
        log.exception("Agent request failed")
        raise HTTPException(
            status_code=500,
            detail="The health assistant could not complete this request.",
        ) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
