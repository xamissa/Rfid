from django.test import TestCase

from bridge_core.final_runtime_orchestrator import (
    FinalRuntimeOrchestrator,
)
from bridge_core.final_runtime_state import (
    RuntimeState,
)
from bridge_core.models import ReaderDevice, RFIDSession


class FakeApiClient:
    def __init__(self):
        self.heartbeats = []
        self.acks = []
        self.command_payloads = []

    def heartbeat(self, *, readers):
        self.heartbeats.append(readers)
        return {"ok": True}

    def commands(self):
        return list(self.command_payloads)

    def ack(self, **kwargs):
        self.acks.append(kwargs)
        return {
            "ok": True,
            "state": (
                "active"
                if kwargs["command"] == "start"
                else "reconciling"
            ),
        }


class FakeReaderExecutor:
    def __init__(self):
        self.starts = []
        self.stops = []
        self.fail_start = False
        self.fail_stop = False

    def start(self, *, session_key, reader_code):
        if self.fail_start:
            raise RuntimeError("simulated reader START failure")

        self.starts.append(
            (session_key, reader_code)
        )

    def stop(self, *, session_key, reader_code):
        if self.fail_stop:
            raise RuntimeError("simulated reader STOP failure")

        self.stops.append(
            (session_key, reader_code)
        )


class FinalRuntimeOrchestratorTests(TestCase):
    def setUp(self):
        self.reader = ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            enabled=True,
        )

        self.api = FakeApiClient()
        self.reader_executor = FakeReaderExecutor()

        self.runtime = FinalRuntimeOrchestrator(
            api_client=self.api,
            reader_executor=self.reader_executor,
        )

    def start_payload(self, revision=1):
        return {
            "session_key": "session-001",
            "reader_code": "receiving-door-01",
            "command": "start",
            "revision": revision,
            "picking": "EXWS1/IN/02227",
        }

    def stop_payload(self, revision=2):
        return {
            "session_key": "session-001",
            "reader_code": "receiving-door-01",
            "command": "stop",
            "revision": revision,
            "picking": "EXWS1/IN/02227",
        }

    def test_reboot_starts_offline_without_resuming_reader(self):
        RFIDSession.objects.create(
            external_session_key="stale-local-session",
            device=self.reader,
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_record_id=0,
            status=RFIDSession.Status.ACTIVE,
        )

        runtime = FinalRuntimeOrchestrator(
            api_client=self.api,
            reader_executor=self.reader_executor,
        )

        self.assertEqual(
            runtime.runtime.state,
            RuntimeState.OFFLINE,
        )
        self.assertIsNone(
            runtime.runtime.session_key
        )
        self.assertEqual(
            self.reader_executor.starts,
            [],
        )

    def test_initial_heartbeat_reports_offline_unverified(self):
        self.runtime.heartbeat()

        heartbeat = self.api.heartbeats[-1][0]

        self.assertEqual(
            heartbeat["reader_code"],
            "receiving-door-01",
        )
        self.assertEqual(
            heartbeat["state"],
            "offline",
        )
        self.assertIsNone(
            heartbeat["session_key"]
        )
        self.assertIn(
            "not yet verified",
            heartbeat["error"],
        )

    def test_verified_reader_can_transition_to_idle(self):
        runtime = self.runtime.mark_reader_verified_idle()

        self.assertEqual(
            runtime.state,
            RuntimeState.IDLE,
        )
        self.assertIsNone(
            runtime.session_key,
        )
        self.assertIsNone(
            runtime.error,
        )

        self.runtime.heartbeat()

        self.assertEqual(
            self.api.heartbeats[-1][0],
            {
                "reader_code": "receiving-door-01",
                "state": "idle",
                "session_key": None,
                "error": None,
            },
        )

    def test_start_command_creates_session_starts_reader_and_acks(self):
        self.runtime.mark_reader_verified_idle()
        self.api.command_payloads = [
            self.start_payload()
        ]

        results = self.runtime.poll_commands()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(
            self.runtime.runtime.state,
            RuntimeState.READING,
        )
        self.assertEqual(
            self.reader_executor.starts,
            [
                (
                    "session-001",
                    "receiving-door-01",
                )
            ],
        )
        self.assertEqual(
            RFIDSession.objects.filter(
                external_session_key="session-001"
            ).count(),
            1,
        )
        self.assertTrue(
            self.api.acks[-1]["success"]
        )

    def test_stop_command_stops_reader_closes_session_and_acks(self):
        self.runtime.mark_reader_verified_idle()
        self.api.command_payloads = [
            self.start_payload()
        ]
        self.runtime.poll_commands()

        self.api.command_payloads = [
            self.stop_payload()
        ]
        results = self.runtime.poll_commands()

        self.assertTrue(results[0].success)
        self.assertEqual(
            self.runtime.runtime.state,
            RuntimeState.IDLE,
        )

        session = RFIDSession.objects.get(
            external_session_key="session-001"
        )

        self.assertEqual(
            session.status,
            RFIDSession.Status.CLOSED,
        )
        self.assertTrue(
            self.api.acks[-1]["success"]
        )

    def test_start_failure_fails_closed(self):
        self.runtime.mark_reader_verified_idle()
        self.reader_executor.fail_start = True
        self.api.command_payloads = [
            self.start_payload()
        ]

        results = self.runtime.poll_commands()

        self.assertFalse(results[0].success)
        self.assertEqual(
            self.runtime.runtime.state,
            RuntimeState.DEGRADED,
        )
        self.assertFalse(
            self.api.acks[-1]["success"]
        )

    def test_stop_failure_fails_closed(self):
        self.runtime.mark_reader_verified_idle()
        self.api.command_payloads = [
            self.start_payload()
        ]
        self.runtime.poll_commands()

        self.reader_executor.fail_stop = True
        self.api.command_payloads = [
            self.stop_payload()
        ]

        results = self.runtime.poll_commands()

        self.assertFalse(results[0].success)
        self.assertEqual(
            self.runtime.runtime.state,
            RuntimeState.DEGRADED,
        )
        self.assertFalse(
            self.api.acks[-1]["success"]
        )

    def test_other_reader_command_is_ignored(self):
        self.api.command_payloads = [
            {
                "session_key": "session-x",
                "reader_code": "dispatch-door-01",
                "command": "start",
                "revision": 1,
                "picking": "OUT/001",
            }
        ]

        results = self.runtime.poll_commands()

        self.assertEqual(results, ())
        self.assertEqual(
            self.reader_executor.starts,
            [],
        )
