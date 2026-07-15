"""Contract tests for model-generated patient retrieval plans."""

import unittest

from google.adk.tools.function_tool import FunctionTool

from agent.context_search import PatientQueryPlan, build_search_plan
from agent.tools import search_patient_context


class PatientContextToolSchemaTests(unittest.TestCase):
    def test_adk_exposes_nested_pydantic_plan_to_gemini(self):
        declaration = FunctionTool(search_patient_context)._get_declaration()
        schema = declaration.parameters_json_schema

        self.assertEqual(schema["required"], ["question", "plan"])
        plan_schema = schema["$defs"]["PatientQueryPlan"]
        self.assertIn("intents", plan_schema["required"])
        self.assertEqual(
            plan_schema["properties"]["operation"]["enum"],
            ["search", "current_medications", "lab_series", "timeline"],
        )

    def test_date_boundaries_are_validated_without_question_parsing(self):
        with self.assertRaises(ValueError):
            PatientQueryPlan(intents=["visit"], date_start="June 2026")

    def test_adk_runtime_dictionary_is_validated_into_the_typed_plan(self):
        plan = build_search_plan(
            "What allergies and immunizations are recorded?",
            {
                "intents": ["allergy", "immunization"],
                "concepts": ["allergies", "immunizations"],
                "scope": "focused",
                "operation": "search",
                "output_mode": "summary",
            },
        )

        self.assertEqual(plan.intents, ["allergy", "immunization"])
        self.assertIn("AllergyIntolerance", plan.resource_types)
        self.assertIn("Immunization", plan.resource_types)


if __name__ == "__main__":
    unittest.main()
