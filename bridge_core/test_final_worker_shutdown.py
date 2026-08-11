from types import SimpleNamespace
from unittest.mock import Mock

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from bridge_core.management.commands.run_final_rfid_worker import (
    Command,
)


class FinalWorkerControlledShutdownTests(SimpleTestCase):
    def test_inactive_reader_closes_without_stop(self):
        executor = SimpleNamespace(
            is_active=False,
            active_session_key=None,
            close=Mock(),
            stop=Mock(),
        )

        worker = SimpleNamespace(
            drain_pending_tags=Mock()
        )

        result = Command._safe_shutdown_reader(
            reader_executor=executor,
            worker=worker,
            reader_code="receiving-door-01",
        )

        self.assertFalse(result)
        executor.close.assert_called_once_with()
        executor.stop.assert_not_called()
        worker.drain_pending_tags.assert_not_called()

    def test_active_reader_is_stopped_and_final_tags_are_drained(self):
        executor = SimpleNamespace(
            is_active=True,
            active_session_key="session-shutdown-1",
            close=Mock(),
            stop=Mock(),
        )

        worker = SimpleNamespace(
            drain_pending_tags=Mock()
        )

        result = Command._safe_shutdown_reader(
            reader_executor=executor,
            worker=worker,
            reader_code="receiving-door-01",
        )

        self.assertTrue(result)

        executor.stop.assert_called_once_with(
            session_key="session-shutdown-1",
            reader_code="receiving-door-01",
        )

        worker.drain_pending_tags.assert_called_once_with(
            session_key="session-shutdown-1",
            reader_code="receiving-door-01",
        )

    def test_active_connection_without_session_key_fails_closed(self):
        executor = SimpleNamespace(
            is_active=True,
            active_session_key=None,
            close=Mock(),
            stop=Mock(),
        )

        worker = SimpleNamespace(
            drain_pending_tags=Mock()
        )

        with self.assertRaisesMessage(
            CommandError,
            "without a session key",
        ):
            Command._safe_shutdown_reader(
                reader_executor=executor,
                worker=worker,
                reader_code="receiving-door-01",
            )

        executor.close.assert_called_once_with()
        executor.stop.assert_not_called()
        worker.drain_pending_tags.assert_not_called()

    def test_stop_failure_fails_closed_without_tag_drain(self):
        executor = SimpleNamespace(
            is_active=True,
            active_session_key="session-shutdown-2",
            close=Mock(),
            stop=Mock(
                side_effect=RuntimeError(
                    "simulated STOP failure"
                )
            ),
        )

        worker = SimpleNamespace(
            drain_pending_tags=Mock()
        )

        with self.assertRaisesMessage(
            CommandError,
            "Failed to verify physical RFID STOP",
        ):
            Command._safe_shutdown_reader(
                reader_executor=executor,
                worker=worker,
                reader_code="receiving-door-01",
            )

        worker.drain_pending_tags.assert_not_called()

    def test_tag_drain_failure_reports_physical_stop_completed(self):
        executor = SimpleNamespace(
            is_active=True,
            active_session_key="session-shutdown-3",
            close=Mock(),
            stop=Mock(),
        )

        worker = SimpleNamespace(
            drain_pending_tags=Mock(
                side_effect=RuntimeError(
                    "simulated persistence failure"
                )
            )
        )

        with self.assertRaisesMessage(
            CommandError,
            "reader stopped, but final buffered tag",
        ):
            Command._safe_shutdown_reader(
                reader_executor=executor,
                worker=worker,
                reader_code="receiving-door-01",
            )

        executor.stop.assert_called_once_with(
            session_key="session-shutdown-3",
            reader_code="receiving-door-01",
        )

    def test_sigterm_handler_enters_keyboard_interrupt_path(self):
        with self.assertRaises(KeyboardInterrupt):
            Command._handle_termination_signal(
                15,
                None,
            )
