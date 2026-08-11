from unittest import TestCase

from bridge_core.final_runtime_state import (
    FINAL_GATEWAY_CODE,
    FINAL_RECEIVING_READER_CODE,
    LocalReaderRuntime,
    OdooRFIDCommand,
    RuntimeCommand,
    RuntimeState,
    RuntimeStateError,
)


class FinalRuntimeStateTests(TestCase):
    def make_command(
        self,
        *,
        command="start",
        revision=1,
        session_key="session-001",
        reader_code=FINAL_RECEIVING_READER_CODE,
    ):
        return OdooRFIDCommand.from_payload(
            {
                "session_key": session_key,
                "reader_code": reader_code,
                "command": command,
                "revision": revision,
                "picking": "EXWS1/IN/02227",
            }
        )

    def test_final_identity_constants(self):
        self.assertEqual(
            FINAL_GATEWAY_CODE,
            "RFID-GW-01",
        )
        self.assertEqual(
            FINAL_RECEIVING_READER_CODE,
            "receiving-door-01",
        )

    def test_start_transition(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE
        )

        transition = runtime.plan_command(
            self.make_command()
        )

        self.assertEqual(
            transition.after.state,
            RuntimeState.STARTING,
        )
        self.assertEqual(
            transition.after.session_key,
            "session-001",
        )

        reading = transition.after.mark_reader_started()

        self.assertEqual(
            reading.state,
            RuntimeState.READING,
        )

    def test_stop_transition_requires_matching_session(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE,
            state=RuntimeState.READING,
            session_key="session-001",
            last_command_revision=1,
        )

        transition = runtime.plan_command(
            self.make_command(
                command="stop",
                revision=2,
            )
        )

        self.assertEqual(
            transition.after.state,
            RuntimeState.STOPPING,
        )

        idle = transition.after.mark_reader_stopped()

        self.assertEqual(
            idle.state,
            RuntimeState.IDLE,
        )
        self.assertIsNone(idle.session_key)

    def test_wrong_reader_is_rejected(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE
        )

        with self.assertRaises(RuntimeStateError):
            runtime.plan_command(
                self.make_command(
                    reader_code="dispatch-door-01",
                )
            )

    def test_start_while_reading_is_rejected(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE,
            state=RuntimeState.READING,
            session_key="session-001",
            last_command_revision=1,
        )

        with self.assertRaises(RuntimeStateError):
            runtime.plan_command(
                self.make_command(
                    revision=2,
                    session_key="session-002",
                )
            )

    def test_stop_for_wrong_session_is_rejected(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE,
            state=RuntimeState.READING,
            session_key="session-001",
            last_command_revision=1,
        )

        with self.assertRaises(RuntimeStateError):
            runtime.plan_command(
                self.make_command(
                    command="stop",
                    revision=2,
                    session_key="session-OTHER",
                )
            )

    def test_duplicate_revision_is_idempotent(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE,
            state=RuntimeState.STARTING,
            session_key="session-001",
            last_command_revision=1,
        )

        transition = runtime.plan_command(
            self.make_command(
                command="start",
                revision=1,
            )
        )

        self.assertTrue(transition.duplicate)
        self.assertEqual(
            transition.before,
            transition.after,
        )

    def test_old_revision_is_rejected(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE,
            state=RuntimeState.READING,
            session_key="session-001",
            last_command_revision=3,
        )

        with self.assertRaises(RuntimeStateError):
            runtime.plan_command(
                self.make_command(
                    command="stop",
                    revision=2,
                )
            )

    def test_abort_moves_to_stopping(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE,
            state=RuntimeState.READING,
            session_key="session-001",
            last_command_revision=1,
        )

        transition = runtime.plan_command(
            self.make_command(
                command="abort",
                revision=2,
            )
        )

        self.assertEqual(
            transition.after.state,
            RuntimeState.STOPPING,
        )

    def test_heartbeat_payload(self):
        runtime = LocalReaderRuntime(
            reader_code=FINAL_RECEIVING_READER_CODE,
            state=RuntimeState.READING,
            session_key="session-001",
        )

        self.assertEqual(
            runtime.heartbeat_payload(),
            {
                "reader_code": "receiving-door-01",
                "state": "reading",
                "session_key": "session-001",
                "error": None,
            },
        )

    def test_unknown_command_rejected(self):
        with self.assertRaises(RuntimeStateError):
            OdooRFIDCommand.from_payload(
                {
                    "session_key": "session-001",
                    "reader_code": "receiving-door-01",
                    "command": "explode",
                    "revision": 1,
                }
            )

    def test_command_enum_values_match_odoo(self):
        self.assertEqual(
            RuntimeCommand.START.value,
            "start",
        )
        self.assertEqual(
            RuntimeCommand.STOP.value,
            "stop",
        )
        self.assertEqual(
            RuntimeCommand.ABORT.value,
            "abort",
        )
