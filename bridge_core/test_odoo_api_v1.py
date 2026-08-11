import json
from unittest import TestCase

from bridge_core.odoo_api_v1 import (
    OdooRFIDApiClient,
    OdooRFIDApiConfigurationError,
    OdooRFIDApiProtocolError,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class RecordingOpener:
    def __init__(self, response_payload):
        self.response_payload = response_payload
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(
            {
                "request": request,
                "timeout": timeout,
            }
        )
        return FakeResponse(self.response_payload)


class RecordingOpenerFactory:
    def __init__(self, response_payload):
        self.opener = RecordingOpener(response_payload)
        self.verify_tls_values = []

    def __call__(self, *, verify_tls):
        self.verify_tls_values.append(verify_tls)
        return self.opener


class OdooRFIDApiClientTests(TestCase):
    def build_client(self, response_payload):
        factory = RecordingOpenerFactory(
            response_payload
        )

        client = OdooRFIDApiClient(
            base_url="https://staging.example.com",
            bearer_token="test-secret-api-key",
            gateway_code="RFID-GW-01",
            timeout_seconds=7,
            verify_tls=True,
            opener_factory=factory,
        )

        return client, factory

    @staticmethod
    def decode_request(factory):
        recorded = factory.opener.requests[-1]
        request = recorded["request"]

        return (
            request,
            recorded["timeout"],
            json.loads(request.data.decode("utf-8")),
        )

    def test_commands_contract(self):
        client, factory = self.build_client(
            {
                "jsonrpc": "2.0",
                "id": None,
                "result": {
                    "ok": True,
                    "commands": [
                        {
                            "session_key": "session-123",
                            "reader_code": "receiving-door-01",
                            "command": "start",
                            "revision": 1,
                            "picking": "WH/IN/00001",
                        }
                    ],
                },
            }
        )

        commands = client.commands()

        self.assertEqual(len(commands), 1)
        self.assertEqual(
            commands[0]["reader_code"],
            "receiving-door-01",
        )

        request, timeout, body = self.decode_request(factory)

        self.assertEqual(
            request.full_url,
            "https://staging.example.com/api/rfid/v1/commands",
        )
        self.assertEqual(timeout, 7)
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer test-secret-api-key",
        )
        self.assertEqual(
            body["params"],
            {"gateway_code": "RFID-GW-01"},
        )

    def test_heartbeat_contract(self):
        client, factory = self.build_client(
            {
                "jsonrpc": "2.0",
                "id": None,
                "result": {
                    "ok": True,
                    "server_time": "2026-08-11 14:00:00",
                },
            }
        )

        result = client.heartbeat(
            readers=[
                {
                    "reader_code": "receiving-door-01",
                    "state": "idle",
                    "session_key": None,
                    "error": None,
                }
            ],
            software_version="test-version",
            reported_address="192.168.1.46",
        )

        self.assertTrue(result["ok"])

        _, _, body = self.decode_request(factory)

        self.assertEqual(
            body["params"]["gateway_code"],
            "RFID-GW-01",
        )
        self.assertEqual(
            body["params"]["readers"][0]["reader_code"],
            "receiving-door-01",
        )

    def test_ack_contract(self):
        client, factory = self.build_client(
            {
                "jsonrpc": "2.0",
                "id": None,
                "result": {
                    "ok": True,
                    "state": "active",
                },
            }
        )

        result = client.ack(
            session_key="session-123",
            reader_code="receiving-door-01",
            command="start",
            revision=1,
            success=True,
            message="Reader started.",
        )

        self.assertEqual(result["state"], "active")

        _, _, body = self.decode_request(factory)

        self.assertEqual(
            body["params"]["command"],
            "start",
        )
        self.assertEqual(
            body["params"]["revision"],
            1,
        )
        self.assertTrue(
            body["params"]["success"]
        )

    def test_events_contract(self):
        client, factory = self.build_client(
            {
                "jsonrpc": "2.0",
                "id": None,
                "result": {
                    "ok": True,
                    "state": "active",
                    "results": [
                        {
                            "event_uuid": "event-123",
                            "epc": "E2000017221101441890ABCD",
                            "outcome": "accepted",
                            "classification": "matched",
                        }
                    ],
                },
            }
        )

        result = client.events(
            session_key="session-123",
            reader_code="receiving-door-01",
            events=[
                {
                    "event_uuid": "event-123",
                    "epc": "E2000017221101441890ABCD",
                    "raw_read_count": 1,
                }
            ],
        )

        self.assertTrue(result["ok"])

        _, _, body = self.decode_request(factory)

        self.assertEqual(
            body["params"]["session_key"],
            "session-123",
        )
        self.assertEqual(
            len(body["params"]["events"]),
            1,
        )

    def test_requires_https(self):
        with self.assertRaises(
            OdooRFIDApiConfigurationError
        ):
            OdooRFIDApiClient(
                base_url="http://staging.example.com",
                bearer_token="token",
                gateway_code="RFID-GW-01",
            )

    def test_rejected_commands_response_fails_closed(self):
        client, _ = self.build_client(
            {
                "jsonrpc": "2.0",
                "id": None,
                "result": {
                    "ok": False,
                    "error": "unknown_gateway",
                },
            }
        )

        with self.assertRaises(
            OdooRFIDApiProtocolError
        ):
            client.commands()
