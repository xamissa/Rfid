from django.test import TestCase

from bridge_core.final_runtime_orchestrator import (
    FinalRuntimeOrchestrator,
)
from bridge_core.final_runtime_state import (
    OdooRFIDCommand,
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
        self._active_session_key = None

    @property
    def is_active(self):
        return self._active_session_key is not None

    @property
    def active_session_key(self):
        return self._active_session_key

    def start(self, *, session_key, reader_code):
        if self.fail_start:
            raise RuntimeError("simulated reader START failure")

        self.starts.append(
            (session_key, reader_code)
        )
        self._active_session_key = session_key

    def stop(self, *, session_key, reader_code):
        if self.fail_stop:
            raise RuntimeError("simulated reader STOP failure")

        self.stops.append(
            (session_key, reader_code)
        )
        self._active_session_key = None


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

    def test_start_failure_fails_closed_and_cancels_new_session(self):
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

        session = RFIDSession.objects.get(
            external_session_key="session-001"
        )

        self.assertEqual(
            session.status,
            RFIDSession.Status.CANCELLED,
        )

        self.assertIsNotNone(
            session.closed_at,
        )

    def test_start_failure_preserves_reused_active_session(self):
        RFIDSession.objects.create(
            external_session_key="session-001",
            device=self.reader,
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_model="stock.picking",
            odoo_record_id=0,
            odoo_reference="EXWS1/IN/02227",
            status=RFIDSession.Status.ACTIVE,
        )

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

        session = RFIDSession.objects.get(
            external_session_key="session-001"
        )

        self.assertEqual(
            session.status,
            RFIDSession.Status.ACTIVE,
        )

        self.assertIsNone(
            session.closed_at,
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


class FinalRuntimePreCloseHookTests(TestCase):
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
        self.executor = FakeReaderExecutor()
        self.hook_calls = []

        self.orchestrator = FinalRuntimeOrchestrator(
            api_client=self.api,
            reader_executor=self.executor,
            before_session_close=self.before_close,
        )

        self.orchestrator.mark_reader_verified_idle()

    def before_close(
        self,
        *,
        session_key,
        reader_code,
    ):
        session = RFIDSession.objects.get(
            external_session_key=session_key
        )

        self.assertEqual(
            session.status,
            RFIDSession.Status.ACTIVE,
        )

        self.hook_calls.append(
            (session_key, reader_code)
        )

    def test_stop_hook_runs_before_local_session_closes(self):
        self.api.command_payloads = [
            {
                "session_key": "session-hook",
                "reader_code": "receiving-door-01",
                "command": "start",
                "revision": 1,
                "picking": "EXWS1/IN/02227",
            }
        ]
        self.orchestrator.poll_commands()

        self.api.command_payloads = [
            {
                "session_key": "session-hook",
                "reader_code": "receiving-door-01",
                "command": "stop",
                "revision": 2,
                "picking": "EXWS1/IN/02227",
            }
        ]

        result = self.orchestrator.poll_commands()

        self.assertTrue(result[0].success)
        self.assertEqual(
            self.hook_calls,
            [
                (
                    "session-hook",
                    "receiving-door-01",
                )
            ],
        )

        session = RFIDSession.objects.get(
            external_session_key="session-hook"
        )

        self.assertEqual(
            session.status,
            RFIDSession.Status.CLOSED,
        )


class FinalRuntimeLostAckRecoveryTests(TestCase):
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
        self.executor = FakeReaderExecutor()

        self.orchestrator = FinalRuntimeOrchestrator(
            api_client=self.api,
            reader_executor=self.executor,
        )

        self.orchestrator.mark_reader_verified_idle()

    def start_command(self):
        return OdooRFIDCommand.from_payload(
            {
                "session_key": "session-ack",
                "reader_code": "receiving-door-01",
                "command": "start",
                "revision": 1,
                "picking": "EXWS1/IN/02227",
            }
        )

    def stop_command(self):
        return OdooRFIDCommand.from_payload(
            {
                "session_key": "session-ack",
                "reader_code": "receiving-door-01",
                "command": "stop",
                "revision": 2,
                "picking": "EXWS1/IN/02227",
            }
        )

    def test_lost_start_ack_is_resent_without_second_start(self):
        command = self.start_command()

        original_ack = self.api.ack
        failure_used = False

        def flaky_ack(**kwargs):
            nonlocal failure_used

            if (
                not failure_used
                and kwargs["command"] == "start"
                and kwargs["success"] is True
            ):
                failure_used = True
                raise RuntimeError(
                    "simulated ACK network loss"
                )

            return original_ack(**kwargs)

        self.api.ack = flaky_ack

        first = self.orchestrator.process_command(
            command
        )

        self.assertFalse(
            first.success
        )
        self.assertEqual(
            self.orchestrator.runtime.state,
            RuntimeState.READING,
        )
        self.assertEqual(
            len(self.executor.starts),
            1,
        )

        second = self.orchestrator.process_command(
            command
        )

        self.assertTrue(
            second.success
        )
        self.assertEqual(
            len(self.executor.starts),
            1,
        )
        self.assertTrue(
            self.api.acks[-1]["success"]
        )

    def test_lost_stop_ack_is_resent_without_second_stop(self):
        start = self.start_command()

        start_result = (
            self.orchestrator.process_command(
                start
            )
        )

        self.assertTrue(
            start_result.success
        )

        command = self.stop_command()

        original_ack = self.api.ack
        failure_used = False

        def flaky_ack(**kwargs):
            nonlocal failure_used

            if (
                not failure_used
                and kwargs["command"] == "stop"
                and kwargs["success"] is True
            ):
                failure_used = True
                raise RuntimeError(
                    "simulated ACK network loss"
                )

            return original_ack(**kwargs)

        self.api.ack = flaky_ack

        first = self.orchestrator.process_command(
            command
        )

        self.assertFalse(
            first.success
        )
        self.assertEqual(
            self.orchestrator.runtime.state,
            RuntimeState.IDLE,
        )
        self.assertEqual(
            len(self.executor.stops),
            1,
        )

        session = RFIDSession.objects.get(
            external_session_key="session-ack"
        )

        self.assertEqual(
            session.status,
            RFIDSession.Status.CLOSED,
        )

        second = self.orchestrator.process_command(
            command
        )

        self.assertTrue(
            second.success
        )
        self.assertEqual(
            len(self.executor.stops),
            1,
        )
        self.assertTrue(
            self.api.acks[-1]["success"]
        )


class FinalRuntimeCaptureHookTests(TestCase):
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
        self.executor = FakeReaderExecutor()
        self.start_capture_calls = []
        self.stop_capture_calls = []

        self.orchestrator = FinalRuntimeOrchestrator(
            api_client=self.api,
            reader_executor=self.executor,
            after_reader_start=self.after_start,
            before_reader_stop=self.before_stop,
        )

        self.orchestrator.mark_reader_verified_idle()

    def after_start(
        self,
        *,
        session_key,
        reader_code,
    ):
        self.start_capture_calls.append(
            (session_key, reader_code)
        )

    def before_stop(
        self,
        *,
        session_key,
        reader_code,
    ):
        self.stop_capture_calls.append(
            (session_key, reader_code)
        )

    def start_payload(self):
        return {
            "session_key": "capture-session",
            "reader_code": "receiving-door-01",
            "command": "start",
            "revision": 1,
            "picking": "EXWS1/IN/02227",
        }

    def stop_payload(self):
        return {
            "session_key": "capture-session",
            "reader_code": "receiving-door-01",
            "command": "stop",
            "revision": 2,
            "picking": "EXWS1/IN/02227",
        }

    def test_capture_starts_before_start_success_returns(self):
        self.api.command_payloads = [
            self.start_payload()
        ]

        result = self.orchestrator.poll_commands()

        self.assertTrue(result[0].success)

        self.assertEqual(
            self.start_capture_calls,
            [
                (
                    "capture-session",
                    "receiving-door-01",
                )
            ],
        )

    def test_capture_relinquishes_reader_before_physical_stop(self):
        self.api.command_payloads = [
            self.start_payload()
        ]
        self.orchestrator.poll_commands()

        self.api.command_payloads = [
            self.stop_payload()
        ]
        result = self.orchestrator.poll_commands()

        self.assertTrue(result[0].success)

        self.assertEqual(
            self.stop_capture_calls,
            [
                (
                    "capture-session",
                    "receiving-door-01",
                )
            ],
        )

        self.assertEqual(
            self.executor.stops,
            [
                (
                    "capture-session",
                    "receiving-door-01",
                )
            ],
        )
