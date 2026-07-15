import unittest
from unittest.mock import Mock, patch

from assistant.terminology import LoincClient


class LoincClientTests(unittest.TestCase):
    def setUp(self):
        self.client = LoincClient()
        self.client.cache.clear_prefix("loinc")

    @staticmethod
    def _response():
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            1,
            ["4548-4"],
            None,
            [[
                "4548-4",
                "Hemoglobin A1c/Hemoglobin.total",
                "MFr",
                "",
                "",
                "",
                "",
                "",
                "",
                "HbA1c MFr Bld",
                "",
                "",
            ]],
        ]
        return response

    @patch("assistant.terminology.requests.get")
    def test_exact_lookup_parses_clinical_tables_display_rows(self, get):
        get.return_value = self._response()

        result = self.client.get_loinc_details("4548-4")

        self.assertEqual(result["loinc_num"], "4548-4")
        self.assertEqual(result["component"], "Hemoglobin A1c/Hemoglobin.total")
        self.assertEqual(result["shortname"], "HbA1c MFr Bld")

    @patch("assistant.terminology.requests.get")
    def test_text_search_uses_returned_code_when_extra_fields_are_null(self, get):
        get.return_value = self._response()

        results = self.client.search_loinc("HbA1c", max_results=5)

        self.assertEqual([item["loinc_num"] for item in results], ["4548-4"])


if __name__ == "__main__":
    unittest.main()
