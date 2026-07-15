"""Tests for source-controlled prototype authentication defaults."""

import unittest
from unittest.mock import patch

from api import prototype_auth


class PrototypeAuthTests(unittest.TestCase):
    def test_missing_credentials_never_authenticate(self):
        with patch.object(prototype_auth, "DEMO_PASSWORD", ""), patch.object(
            prototype_auth, "DEMO_SESSION_TOKEN", ""
        ):
            config = prototype_auth.PrototypeAuthConfig()

            self.assertFalse(config.password_matches(""))
            self.assertFalse(config.password_matches("anything"))
            self.assertIsNone(config.verify_session_token(""))
            with self.assertRaises(RuntimeError):
                config.create_session_token()

    def test_configured_credentials_preserve_the_prototype_flow(self):
        with patch.object(prototype_auth, "DEMO_PASSWORD", "demo-password"), patch.object(
            prototype_auth, "DEMO_SESSION_TOKEN", "random-session-token"
        ):
            config = prototype_auth.PrototypeAuthConfig()

            self.assertTrue(config.password_matches("demo-password"))
            self.assertFalse(config.password_matches("wrong"))
            token = config.create_session_token()
            self.assertEqual(token, "random-session-token")
            self.assertEqual(config.verify_session_token(token), {"sub": "prototype-john"})


if __name__ == "__main__":
    unittest.main()
