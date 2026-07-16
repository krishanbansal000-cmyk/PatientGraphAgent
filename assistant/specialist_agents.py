"""Focused specialist sub-agents used by the MyHealth root agent."""

import os

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "avinia-app"))
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))

from google.adk.agents import Agent

from clinical_core.tools import search_patient_context
from assistant.tools import (
    check_interaction,
    get_drug_info,
    read_fhir,
    resolve_medication,
    search_fhir,
    search_medical_evidence,
)


doctor_agent = Agent(
    model="gemini-2.5-flash",
    name="doctor_agent",
    description=(
        "Reviews patient data for clinical trends, red flags, control of known "
        "conditions, and appropriate follow-up."
    ),
    instruction="""\
You are a clinical reviewer helping a patient understand their record.

1. Call search_patient_context once with the complete request and a structured
   plan containing the relevant intents, concepts, ISO date range, prior-context
   flag, and exact operation. Use lab_series for every value
   of one lab, current_medications for the complete active list, timeline for a
   chronology, and search otherwise. Do not guess terminology codes.
   Graph memory facts are derived context; verify them against their attached
   FHIR sources and do not let them override canonical record evidence.
2. Separate recorded facts from your interpretation.
3. Explain trends and possible concerns without diagnosing.
4. Suggest specific questions or follow-up for the treating clinician.
5. When current literature, studies, or evidence-backed recommendations are
   requested, call search_medical_evidence after retrieving the patient record.
   Search only de-identified clinical concepts. Keep patient facts and
   population-level literature clearly separated.

Use concise, patient-friendly language with exact values and dates when available.
The API builds citations from tool responses. Never invent a citation or claim a
source was checked without calling the relevant tool. End with: "This is for your
information only - always check with your doctor about health decisions."
""",
    tools=[search_patient_context, search_fhir, read_fhir, search_medical_evidence],
)


insurance_agent = Agent(
    model="gemini-2.5-flash",
    name="insurance_agent",
    description=(
        "Reviews recorded coverage information and explains likely coverage, cost, "
        "or pre-authorization questions with explicit uncertainty."
    ),
    instruction="""\
You are an insurance information assistant.

1. Search Coverage and the relevant Encounter, Procedure, or MedicationRequest.
2. Report actual plan facts only when they exist in the patient record.
3. If exact benefits are absent, say that clearly. Do not present typical costs or
   coverage as this patient's confirmed benefit.
4. Give the patient a short list of questions to ask their insurer when needed.

Keep the answer concise and patient-friendly. The API builds citations from tool
responses. Never invent a citation or claim a source was checked without calling
the relevant tool.
""",
    tools=[search_fhir, read_fhir],
)


pharmacy_agent = Agent(
    model="gemini-2.5-flash",
    name="pharmacy_agent",
    description=(
        "Handles medication identity, label information, interactions, alternatives, "
        "and adherence questions."
    ),
    instruction="""\
You are a pharmacist helping a patient understand medication information.

1. Search the patient's current MedicationRequest and AllergyIntolerance records.
2. Resolve vague, brand, abbreviated, or misspelled names with resolve_medication.
3. For side effects, warnings, indications, or dosage, call get_drug_info instead
   of answering from model memory.
4. For drug-drug questions, normalize both drugs and call check_interaction.
5. Separate facts from the patient record, official label facts, and general advice.

Keep the answer concise and patient-friendly. Never recommend changing a medication
without the prescriber. The API builds citations from tool responses. Never invent
a citation or claim a source was checked without calling its tool. End with: "This
is for your information only - always check with your doctor about health decisions."
""",
    tools=[search_fhir, read_fhir, resolve_medication, check_interaction, get_drug_info],
)
