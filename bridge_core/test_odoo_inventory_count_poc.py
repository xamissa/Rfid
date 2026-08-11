import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from bridge_core.models import OperationalConfiguration
from bridge_core.odoo_inventory_count_poc import (
    OdooInventoryCountPocConfigurationError,
    build_odoo_url,
    execute_inventory_count_poc,
    normalize_rfid_tags,
)


class FakeResponse:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def getcode(self):
        return self.code

    def read(self):
        return BytesIO(
            json.dumps(self.payload).encode("utf-8")
        ).read()


class FakeSessionOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class OdooInventoryCountPocClientTests(SimpleTestCase):
    def make_configuration(self, **overrides):
        values = {
            "odoo_inventory_count_poc_enabled": True,
            "odoo_authentication_method": (
                OperationalConfiguration
                .OdooAuthenticationMethod.ODOO_SESSION
            ),
            "odoo_base_url": (
                "https://staging-example.odoo.com"
            ),
            "odoo_database": "staging-database",
            "odoo_client_identifier": (
                "rfid.integration@example.com"
            ),
            "has_odoo_secret": True,
            "get_odoo_secret": Mock(
                return_value="staging-password"
            ),
            "odoo_inventory_count_endpoint": (
                "/api/rfid/inventory_count"
            ),
            "odoo_inventory_count_location_id": 8,
            "odoo_request_timeout_seconds": 15,
            "odoo_verify_tls": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_build_odoo_url_joins_relative_endpoint(self):
        result = build_odoo_url(
            base_url=(
                "https://staging-example.odoo.com/"
            ),
            endpoint="/api/rfid/inventory_count",
        )

        self.assertEqual(
            result,
            (
                "https://staging-example.odoo.com/"
                "api/rfid/inventory_count"
            ),
        )

    def test_embedded_credentials_are_rejected(self):
        with self.assertRaisesMessage(
            OdooInventoryCountPocConfigurationError,
            "may not be embedded",
        ):
            build_odoo_url(
                base_url=(
                    "https://user:secret@example.odoo.com"
                ),
                endpoint="/api/rfid/inventory_count",
            )

    def test_tags_are_trimmed_uppercased_and_deduplicated(self):
        self.assertEqual(
            normalize_rfid_tags(
                [" epc001 ", "EPC001", "", "epc002"]
            ),
            ("EPC001", "EPC002"),
        )

    def test_empty_tag_collection_is_rejected(self):
        with self.assertRaisesMessage(
            OdooInventoryCountPocConfigurationError,
            "At least one RFID tag",
        ):
            normalize_rfid_tags(["", "   "])

    def test_contact_guard_blocks_before_opener_creation(self):
        opener_factory = Mock()
        configuration = self.make_configuration()

        with self.assertRaisesMessage(
            PermissionError,
            "disabled by the gateway",
        ):
            execute_inventory_count_poc(
                configuration=configuration,
                rfid_tags=["EPC001"],
                allow_contact=False,
                opener_factory=opener_factory,
            )

        opener_factory.assert_not_called()

    def test_disabled_poc_blocks_before_opener_creation(self):
        opener_factory = Mock()
        configuration = self.make_configuration(
            odoo_inventory_count_poc_enabled=False
        )

        with self.assertRaisesMessage(
            OdooInventoryCountPocConfigurationError,
            "POC is disabled",
        ):
            execute_inventory_count_poc(
                configuration=configuration,
                rfid_tags=["EPC001"],
                allow_contact=True,
                opener_factory=opener_factory,
            )

        opener_factory.assert_not_called()

    def test_success_authenticates_then_posts_exact_poc_payload(self):
        opener = FakeSessionOpener(
            [
                FakeResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "result": {
                            "uid": 12,
                        },
                    }
                ),
                FakeResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "result": {
                            "status": "success",
                            "total_counted": 2,
                            "unknown_rfid_tags": [],
                        },
                    }
                ),
            ]
        )
        opener_factory = Mock(return_value=opener)
        configuration = self.make_configuration()

        result = execute_inventory_count_poc(
            configuration=configuration,
            rfid_tags=[
                " epc001 ",
                "EPC001",
                "epc002",
            ],
            allow_contact=True,
            opener_factory=opener_factory,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response_code, "200")
        self.assertEqual(result.total_counted, 2)
        self.assertEqual(result.unknown_rfid_tags, ())
        self.assertEqual(len(opener.requests), 2)

        login_request, login_timeout = opener.requests[0]
        inventory_request, inventory_timeout = (
            opener.requests[1]
        )

        self.assertEqual(
            login_request.full_url,
            (
                "https://staging-example.odoo.com/"
                "web/session/authenticate"
            ),
        )
        self.assertEqual(login_request.get_method(), "POST")
        self.assertEqual(login_timeout, 15)

        login_payload = json.loads(
            login_request.data.decode("utf-8")
        )

        self.assertEqual(
            login_payload["params"],
            {
                "db": "staging-database",
                "login": "rfid.integration@example.com",
                "password": "staging-password",
            },
        )

        self.assertEqual(
            inventory_request.full_url,
            (
                "https://staging-example.odoo.com/"
                "api/rfid/inventory_count"
            ),
        )
        self.assertEqual(
            inventory_request.get_method(),
            "POST",
        )
        self.assertEqual(inventory_timeout, 15)

        inventory_payload = json.loads(
            inventory_request.data.decode("utf-8")
        )

        self.assertEqual(
            inventory_payload["params"],
            {
                "location_id": 8,
                "rfid_tags": [
                    "EPC001",
                    "EPC002",
                ],
            },
        )

        opener_factory.assert_called_once_with(
            verify_tls=True
        )

    def test_unknown_tags_are_returned_to_operator(self):
        opener = FakeSessionOpener(
            [
                FakeResponse(
                    {
                        "jsonrpc": "2.0",
                        "result": {"uid": 12},
                    }
                ),
                FakeResponse(
                    {
                        "jsonrpc": "2.0",
                        "result": {
                            "status": "success",
                            "total_counted": 1,
                            "unknown_rfid_tags": [
                                "epc-unknown",
                            ],
                        },
                    }
                ),
            ]
        )

        result = execute_inventory_count_poc(
            configuration=self.make_configuration(),
            rfid_tags=["EPC001", "EPC-UNKNOWN"],
            allow_contact=True,
            opener_factory=Mock(return_value=opener),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.total_counted, 1)
        self.assertEqual(
            result.unknown_rfid_tags,
            ("EPC-UNKNOWN",),
        )

    def test_authentication_without_uid_fails_closed(self):
        opener = FakeSessionOpener(
            [
                FakeResponse(
                    {
                        "jsonrpc": "2.0",
                        "result": {"uid": False},
                    }
                ),
            ]
        )

        result = execute_inventory_count_poc(
            configuration=self.make_configuration(),
            rfid_tags=["EPC001"],
            allow_contact=True,
            opener_factory=Mock(return_value=opener),
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.error_kind,
            "OdooInventoryCountPocProtocolError",
        )
        self.assertIn(
            "valid user ID",
            result.detail,
        )
        self.assertEqual(len(opener.requests), 1)

    def test_jsonrpc_error_is_returned_without_secret(self):
        opener = FakeSessionOpener(
            [
                FakeResponse(
                    {
                        "jsonrpc": "2.0",
                        "error": {
                            "message": "Access denied",
                        },
                    }
                ),
            ]
        )
        configuration = self.make_configuration()

        result = execute_inventory_count_poc(
            configuration=configuration,
            rfid_tags=["EPC001"],
            allow_contact=True,
            opener_factory=Mock(return_value=opener),
        )

        self.assertFalse(result.success)
        self.assertIn("Access denied", result.detail)
        self.assertNotIn(
            "staging-password",
            result.detail,
        )
