from django.test import SimpleTestCase

from config.environment import ConfigurationError
from bridge_core.final_runtime_config import (
    load_final_runtime_configuration,
)


class FinalRuntimeConfigurationTests(SimpleTestCase):
    def base(self):
        return {
            "RFID_ODOO_BASE_URL": "https://example.odoo.com",
            "RFID_ODOO_BEARER_TOKEN": "secret-token",
            "RFID_GATEWAY_CODE": "RFID-GW-01",
        }

    def test_minimum_configuration(self):
        result = load_final_runtime_configuration(
            self.base()
        )

        self.assertEqual(
            result.reader_code,
            "receiving-door-01",
        )
        self.assertEqual(
            result.poll_seconds,
            1.0,
        )
        self.assertTrue(
            result.verify_tls
        )

    def test_missing_bearer_token_fails_closed(self):
        values = self.base()
        del values["RFID_ODOO_BEARER_TOKEN"]

        with self.assertRaises(
            ConfigurationError
        ):
            load_final_runtime_configuration(
                values
            )

    def test_invalid_poll_interval_rejected(self):
        values = self.base()
        values["RFID_CONTROL_POLL_SECONDS"] = "0"

        with self.assertRaises(
            ConfigurationError
        ):
            load_final_runtime_configuration(
                values
            )
