import unittest
from unittest.mock import patch

from assistant.dailymed import DailyMedClient


class DailyMedSelectionTests(unittest.TestCase):
    def test_single_ingredient_label_beats_newer_combination_label(self):
        candidates = [
            {
                "setid": "combo",
                "title": "BISOPROLOL FUMARATE AND HYDROCHLOROTHIAZIDE TABLET [EXAMPLE]",
                "published_date": "Jul 15, 2026",
                "spl_version": "9",
            },
            {
                "setid": "single",
                "title": "HYDROCHLOROTHIAZIDE TABLET [EXAMPLE]",
                "published_date": "Jul 10, 2026",
                "spl_version": "2",
            },
        ]

        selected = DailyMedClient._select_spl(candidates, "hydrochlorothiazide")

        self.assertEqual(selected["setid"], "single")

    def test_combination_label_is_allowed_for_combination_request(self):
        candidates = [
            {
                "setid": "single",
                "title": "HYDROCHLOROTHIAZIDE TABLET [EXAMPLE]",
                "published_date": "Jul 15, 2026",
            },
            {
                "setid": "combo",
                "title": "LISINOPRIL AND HYDROCHLOROTHIAZIDE TABLET [EXAMPLE]",
                "published_date": "Jul 10, 2026",
            },
        ]

        selected = DailyMedClient._select_spl(
            candidates, "lisinopril and hydrochlorothiazide"
        )

        self.assertEqual(selected["setid"], "combo")

    def test_name_candidates_are_considered_when_ingredient_rxcui_returns_combinations(self):
        client = DailyMedClient()
        client.cache.clear_prefix("dailymed")
        combination = {
            "setid": "combo",
            "title": "BISOPROLOL FUMARATE AND HYDROCHLOROTHIAZIDE TABLET [EXAMPLE]",
            "published_date": "Jul 15, 2026",
        }
        single = {
            "setid": "single",
            "title": "HYDROCHLOROTHIAZIDE CAPSULE [EXAMPLE]",
            "published_date": "Jul 10, 2026",
        }
        with (
            patch.object(client, "search_spls_by_rxcui", return_value=[combination]),
            patch.object(client, "search_spls_by_name", return_value=[single]),
            patch.object(client, "get_spl_document", return_value="<xml />"),
            patch.object(
                client,
                "_extract_sections",
                return_value={"adverse_reactions": "Weakness and dizziness."},
            ),
        ):
            result = client.get_drug_info("5487", "hydrochlorothiazide")

        self.assertEqual(result["set_id"], "single")
        self.assertIn("Weakness", result["adverse_reactions"])


if __name__ == "__main__":
    unittest.main()
