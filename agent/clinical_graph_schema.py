"""Typed extraction vocabulary for the patient-scoped clinical memory graph.

FHIR remains authoritative. These models constrain Graphiti's LLM extraction;
they are not a replacement for the exact FHIR resources or their provenance.
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Type

from pydantic import BaseModel, Field


class PatientRecordSubject(BaseModel):
    """The patient whose isolated memory partition is being processed."""

    stable_key: str | None = Field(default=None, description="Non-identifying patient partition key")


class ClinicalEncounter(BaseModel):
    """A clinical visit, emergency presentation, admission, or other care encounter."""

    fhir_reference: str | None = Field(default=None, description="Exact Encounter/type identifier")
    encounter_class: str | None = Field(default=None, description="FHIR encounter class or setting")
    status: str | None = Field(default=None, description="FHIR encounter status")
    started_at: str | None = Field(default=None, description="Encounter start time from the source")
    ended_at: str | None = Field(default=None, description="Encounter end time from the source")


class ClinicalCondition(BaseModel):
    """A diagnosed, suspected, active, resolved, or historical health condition."""

    fhir_reference: str | None = Field(default=None, description="Exact Condition/type identifier")
    terminology_system: str | None = Field(default=None, description="Code system such as SNOMED CT or ICD-10")
    terminology_code: str | None = Field(default=None, description="Condition code from the source")
    clinical_status: str | None = Field(default=None, description="Active, resolved, recurrence, or other source status")
    onset_at: str | None = Field(default=None, description="Condition onset time when recorded")


class MedicationTherapy(BaseModel):
    """A medication order, statement, dispense, or administration in the patient record."""

    fhir_reference: str | None = Field(default=None, description="Exact medication resource identifier")
    rxnorm_code: str | None = Field(default=None, description="RxNorm concept identifier when present")
    status: str | None = Field(default=None, description="Medication resource status")
    dose: str | None = Field(default=None, description="Dose and strength exactly as recorded")
    route: str | None = Field(default=None, description="Medication route exactly as recorded")
    started_at: str | None = Field(default=None, description="Medication start or authored time")
    ended_at: str | None = Field(default=None, description="Medication end time when recorded")


class ClinicalObservation(BaseModel):
    """A laboratory result, vital sign, measurement, or clinical assessment."""

    fhir_reference: str | None = Field(default=None, description="Exact Observation or DiagnosticReport identifier")
    loinc_code: str | None = Field(default=None, description="LOINC code when present")
    value: str | None = Field(default=None, description="Observed value exactly as recorded")
    unit: str | None = Field(default=None, description="Unit exactly as recorded")
    interpretation: str | None = Field(default=None, description="Source interpretation such as high or low")
    observed_at: str | None = Field(default=None, description="Clinical observation time")


class PatientReportedSymptom(BaseModel):
    """A symptom or complaint reported by or documented for the patient."""

    fhir_reference: str | None = Field(default=None, description="Exact supporting FHIR resource identifier")
    terminology_system: str | None = Field(default=None, description="Symptom terminology system")
    terminology_code: str | None = Field(default=None, description="Symptom code from the source")
    onset_at: str | None = Field(default=None, description="Symptom onset time when recorded")
    severity: str | None = Field(default=None, description="Severity exactly as recorded")


class ClinicalProcedure(BaseModel):
    """A diagnostic, therapeutic, or preventive procedure performed for the patient."""

    fhir_reference: str | None = Field(default=None, description="Exact Procedure identifier")
    terminology_system: str | None = Field(default=None, description="Procedure terminology system")
    terminology_code: str | None = Field(default=None, description="Procedure code from the source")
    status: str | None = Field(default=None, description="Procedure status")
    performed_at: str | None = Field(default=None, description="Procedure time from the source")


class ClinicalCarePlan(BaseModel):
    """A documented care plan, goal, or planned clinical activity."""

    fhir_reference: str | None = Field(default=None, description="Exact CarePlan identifier")
    status: str | None = Field(default=None, description="Care plan status")
    intent: str | None = Field(default=None, description="Care plan intent")
    documented_at: str | None = Field(default=None, description="Care plan creation time")


class ClinicalAllergy(BaseModel):
    """A recorded allergy or intolerance; never a diagnosed condition."""

    fhir_reference: str | None = Field(default=None, description="Exact AllergyIntolerance identifier")
    terminology_system: str | None = Field(default=None, description="Allergy terminology system")
    terminology_code: str | None = Field(default=None, description="Allergy code from the source")
    clinical_status: str | None = Field(default=None, description="Active, inactive, or resolved status")
    criticality: str | None = Field(default=None, description="FHIR allergy criticality when recorded")


class ClinicalImmunization(BaseModel):
    """A vaccine administration recorded in the patient record."""

    fhir_reference: str | None = Field(default=None, description="Exact Immunization identifier")
    terminology_system: str | None = Field(default=None, description="Vaccine terminology system")
    terminology_code: str | None = Field(default=None, description="Vaccine code from the source")
    status: str | None = Field(default=None, description="Immunization status")
    occurred_at: str | None = Field(default=None, description="Administration date from the source")


class HasEncounter(BaseModel):
    """Links the patient record subject to a documented encounter."""


class HasCondition(BaseModel):
    """Links the patient record subject to a documented condition."""

    status: str | None = Field(default=None, description="Condition status at the stated time")


class HasMedicationTherapy(BaseModel):
    """Links the patient to an order, statement, dispense, or administration."""

    status: str | None = Field(default=None, description="Medication status at the stated time")


class HasClinicalResult(BaseModel):
    """Links the patient record subject to an observation or clinical result."""


class ReportedSymptom(BaseModel):
    """Links the patient record subject to a documented symptom."""


class UnderwentProcedure(BaseModel):
    """Links the patient record subject to a documented procedure."""


class HasCarePlan(BaseModel):
    """Links the patient record subject to a documented care plan."""


class HasAllergy(BaseModel):
    """Links the patient record subject to a recorded allergy or intolerance."""


class ReceivedImmunization(BaseModel):
    """Links the patient record subject to a recorded immunization."""


class OccurredDuringEncounter(BaseModel):
    """Links a clinical fact to the encounter during which it was documented."""


ENTITY_TYPES: Dict[str, Type[BaseModel]] = {
    "PatientRecordSubject": PatientRecordSubject,
    "ClinicalEncounter": ClinicalEncounter,
    "ClinicalCondition": ClinicalCondition,
    "MedicationTherapy": MedicationTherapy,
    "ClinicalObservation": ClinicalObservation,
    "PatientReportedSymptom": PatientReportedSymptom,
    "ClinicalProcedure": ClinicalProcedure,
    "ClinicalCarePlan": ClinicalCarePlan,
    "ClinicalAllergy": ClinicalAllergy,
    "ClinicalImmunization": ClinicalImmunization,
}

EDGE_TYPES: Dict[str, Type[BaseModel]] = {
    "HAS_ENCOUNTER": HasEncounter,
    "HAS_CONDITION": HasCondition,
    "HAS_MEDICATION_THERAPY": HasMedicationTherapy,
    "HAS_CLINICAL_RESULT": HasClinicalResult,
    "REPORTED_SYMPTOM": ReportedSymptom,
    "UNDERWENT_PROCEDURE": UnderwentProcedure,
    "HAS_CARE_PLAN": HasCarePlan,
    "HAS_ALLERGY": HasAllergy,
    "RECEIVED_IMMUNIZATION": ReceivedImmunization,
    "OCCURRED_DURING_ENCOUNTER": OccurredDuringEncounter,
}

EDGE_TYPE_MAP: Dict[Tuple[str, str], List[str]] = {
    ("PatientRecordSubject", "ClinicalEncounter"): ["HAS_ENCOUNTER"],
    ("PatientRecordSubject", "ClinicalCondition"): ["HAS_CONDITION"],
    ("PatientRecordSubject", "MedicationTherapy"): ["HAS_MEDICATION_THERAPY"],
    ("PatientRecordSubject", "ClinicalObservation"): ["HAS_CLINICAL_RESULT"],
    ("PatientRecordSubject", "PatientReportedSymptom"): ["REPORTED_SYMPTOM"],
    ("PatientRecordSubject", "ClinicalProcedure"): ["UNDERWENT_PROCEDURE"],
    ("PatientRecordSubject", "ClinicalCarePlan"): ["HAS_CARE_PLAN"],
    ("PatientRecordSubject", "ClinicalAllergy"): ["HAS_ALLERGY"],
    ("PatientRecordSubject", "ClinicalImmunization"): ["RECEIVED_IMMUNIZATION"],
    ("ClinicalCondition", "ClinicalEncounter"): ["OCCURRED_DURING_ENCOUNTER"],
    ("MedicationTherapy", "ClinicalEncounter"): ["OCCURRED_DURING_ENCOUNTER"],
    ("ClinicalObservation", "ClinicalEncounter"): ["OCCURRED_DURING_ENCOUNTER"],
    ("PatientReportedSymptom", "ClinicalEncounter"): ["OCCURRED_DURING_ENCOUNTER"],
    ("ClinicalProcedure", "ClinicalEncounter"): ["OCCURRED_DURING_ENCOUNTER"],
    ("ClinicalImmunization", "ClinicalEncounter"): ["OCCURRED_DURING_ENCOUNTER"],
}

CLINICAL_EXTRACTION_INSTRUCTIONS = """
Extract only facts explicitly present in the episode JSON. Treat the subject named
"this patient" as one PatientRecordSubject within this group. Preserve exact FHIR
references, terminology codes, statuses, and dates provided in the episode.
Use only the supplied entity and relationship types. Every documented Encounter
must use HAS_ENCOUNTER from the patient. AllergyIntolerance must be ClinicalAllergy
with HAS_ALLERGY, never ClinicalCondition. Immunization must be
ClinicalImmunization with RECEIVED_IMMUNIZATION, never ClinicalProcedure or
ClinicalEncounter. MedicationRequest is HAS_MEDICATION_THERAPY, not proof that a
dose was received. Do not compare separate clinical items or generate changes,
trends, diagnoses, causality, treatment indications, interactions, cohorts, or
recommendations. FHIR source references are provenance identifiers and must not
be renamed. Do not treat a model-generated relationship as clinical truth.
""".strip()
