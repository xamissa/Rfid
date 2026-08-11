from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from bridge_core.models import OperationalConfiguration
from bridge_core.odoo_connection import (
    OdooConnectionConfigurationError,
    build_authentication_headers,
    build_connection_test_url,
    execute_odoo_connection_test,
)


class OdooConnectionClientTests(SimpleTestCase):
    def make_configuration(self, **overrides):
        values = {
            "odoo_base_url": (
                "https://staging-example.odoo.com"
            ),
            "odoo_session_endpoint": (
                "/web/session/authenticate"
            ),
            "odoo_database": "staging-db",
            "odoo_authentication_method": (
                OperationalConfiguration
                .OdooAuthenticationMethod
                .ODOO_SESSION
            ),
            "odoo_client_identifier": (
                "rfid-poc@example.test"
            ),
            "odoo_request_timeout_seconds": 10,
            "odoo_verify_tls": True,
            "has_odoo_secret": True,
            "get_odoo_secret": Mock(
                return_value="safe-test-password"
            ),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_relative_endpoint_is_joined_safely(self):
        result = build_connection_test_url(
            base_url="https://staging-example.odoo.com/",
            session_endpoint="/web/session/authenticate",
        )

        self.assertEqual(
            result,
            (
                "https://staging-example.odoo.com/"
                "web/session/authenticate"
            ),
        )

    def test_embedded_credentials_are_rejected(self):
        with self.assertRaisesMessage(
            OdooConnectionConfigurationError,
            "may not be embedded",
        ):
            build_connection_test_url(
                base_url=(
                    "https://user:secret@example.odoo.com"
                ),
                session_endpoint="/web/session/authenticate",
            )

    def test_bearer_header_uses_decrypted_secret(self):
        configuration = self.make_configuration(
            odoo_authentication_method=(
                OperationalConfiguration
                .OdooAuthenticationMethod.BEARER_TOKEN
            ),
            odoo_client_identifier="rfid-bridge",
            get_odoo_secret=Mock(
                return_value="test-bearer-secret"
            ),
        )

        headers = build_authentication_headers(
            configuration=configuration
        )

        self.assertEqual(
            headers["Authorization"],
            "Bearer test-bearer-secret",
        )
        self.assertEqual(
            headers["X-Client-ID"],
            "rfid-bridge",
        )
        self.assertEqual(
            headers["X-Odoo-Database"],
            "staging-db",
        )

    def test_contact_flag_blocks_before_opener(self):
        opener_factory = Mock()
        configuration = self.make_configuration()

        with self.assertRaisesMessage(
            PermissionError,
            "disabled by the gateway",
        ):
            execute_odoo_connection_test(
                configuration=configuration,
                allow_contact=False,
                opener_factory=opener_factory,
            )

        opener_factory.assert_not_called()

    def test_non_session_authentication_is_blocked(self):
        opener_factory = Mock()
        configuration = self.make_configuration(
            odoo_authentication_method=(
                OperationalConfiguration
                .OdooAuthenticationMethod.NONE
            )
        )

        with self.assertRaisesMessage(
            OdooConnectionConfigurationError,
            "requires Odoo username and password",
        ):
            execute_odoo_connection_test(
                configuration=configuration,
                allow_contact=True,
                opener_factory=opener_factory,
            )

        opener_factory.assert_not_called()

    @patch(
        "bridge_core.odoo_connection."
        "authenticate_odoo_session"
    )
    def test_success_authenticates_session_without_rfid_payload(
        self,
        mocked_authenticate,
    ):
        opener = Mock()
        opener_factory = Mock(return_value=opener)
        configuration = self.make_configuration()

        result = execute_odoo_connection_test(
            configuration=configuration,
            allow_contact=True,
            opener_factory=opener_factory,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response_code, "200")
        self.assertIn(
            "session authentication succeeded",
            result.detail.lower(),
        )
        self.assertIn(
            "no rfid event",
            result.detail.lower(),
        )

        opener_factory.assert_called_once_with(
            verify_tls=True
        )
        mocked_authenticate.assert_called_once_with(
            configuration=configuration,
            opener=opener,
        )
