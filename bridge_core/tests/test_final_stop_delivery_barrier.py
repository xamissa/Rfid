from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from bridge_core.final_delivery_barrier import (
    FinalDeliveryBarrierFailed,
    FinalDeliveryBarrierPending,
    require_final_session_delivery_complete,
)
from bridge_core.final_runtime_orchestrator import (
    FinalRuntimeOrchestrator,
)
from bridge_core.final_runtime_state import (
    LocalReaderRuntime,
    OdooRFIDCommand,
    RuntimeCommand,
    RuntimeState,
)
from bridge_core.models import RawRFIDEvent


class FinalDeliveryBarrierTests(SimpleTestCase):
    @staticmethod
    def _mock_states(mocked_objects, states):
        queryset = Mock()
        queryset.values_list.return_value = tuple(
            states
        )
        mocked_objects.filter.return_value = queryset

    @patch(
        "bridge_core.final_delivery_barrier."
        "RawRFIDEvent.objects"
    )
    def test_no_events_allows_stop_ack(
        self,
        mocked_objects,
    ):
        self._mock_states(
            mocked_objects,
            (),
        )

        result = (
            require_final_session_delivery_complete(
                session_key="session-1",
                reader_code="receiving-door-01",
            )
        )

        self.assertEqual(result.total_count, 0)
        self.assertEqual(result.pending_count, 0)
        self.assertEqual(result.failed_count, 0)

    @patch(
        "bridge_core.final_delivery_barrier."
        "RawRFIDEvent.objects"
    )
    def test_all_sent_events_allow_stop_ack(
        self,
        mocked_objects,
    ):
        self._mock_states(
            mocked_objects,
            (
                RawRFIDEvent.QueueState.SENT,
                RawRFIDEvent.QueueState.SENT,
            ),
        )

        result = (
            require_final_session_delivery_complete(
                session_key="session-1",
                reader_code="receiving-door-01",
            )
        )

        self.assertEqual(result.total_count, 2)
        self.assertEqual(result.sent_count, 2)
        self.assertEqual(result.pending_count, 0)

    @patch(
        "bridge_core.final_delivery_barrier."
        "RawRFIDEvent.objects"
    )
    def test_pending_states_block_stop_ack(
        self,
        mocked_objects,
    ):
        self._mock_states(
            mocked_objects,
            (
                RawRFIDEvent.QueueState.QUEUED,
                RawRFIDEvent.QueueState.INFLIGHT,
                RawRFIDEvent.QueueState.RETRY,
                RawRFIDEvent.QueueState.UNASSIGNED,
            ),
        )

        with self.assertRaises(
            FinalDeliveryBarrierPending
        ):
            require_final_session_delivery_complete(
                session_key="session-1",
                reader_code="receiving-door-01",
            )

    @patch(
        "bridge_core.final_delivery_barrier."
        "RawRFIDEvent.objects"
    )
    def test_rejected_or_dead_events_fail_closed(
        self,
        mocked_objects,
    ):
        self._mock_states(
            mocked_objects,
            (
                RawRFIDEvent.QueueState.SENT,
                RawRFIDEvent.QueueState.REJECTED,
                RawRFIDEvent.QueueState.DEAD,
            ),
        )

        with self.assertRaises(
            FinalDeliveryBarrierFailed
        ):
            require_final_session_delivery_complete(
                session_key="session-1",
                reader_code="receiving-door-01",
            )


class FinalStopAckBarrierOrchestratorTests(
    SimpleTestCase
):
    def setUp(self):
        self.api_client = SimpleNamespace(
            ack=Mock(
                return_value={
                    "ok": True,
                }
            )
        )

        self.reader_executor = SimpleNamespace(
            stop=Mock()
        )

        self.before_session_close = Mock()
        self.before_success_ack = Mock()

        self.orchestrator = FinalRuntimeOrchestrator(
            api_client=self.api_client,
            reader_executor=self.reader_executor,
            reader_code="receiving-door-01",
            before_session_close=(
                self.before_session_close
            ),
            before_success_ack=(
                self.before_success_ack
            ),
        )

        self.orchestrator.runtime = (
            LocalReaderRuntime(
                reader_code="receiving-door-01",
                state=RuntimeState.READING,
                session_key="session-1",
                last_command_revision=1,
            )
        )

        self.command = OdooRFIDCommand(
            session_key="session-1",
            reader_code="receiving-door-01",
            command=RuntimeCommand.STOP,
            revision=2,
            picking="WH/IN/00001",
        )

    @patch(
        "bridge_core.final_runtime_orchestrator."
        "close_local_session"
    )
    def test_stop_ack_waits_for_delivery_then_duplicate_retries_only_ack(
        self,
        mocked_close_local_session,
    ):
        self.before_success_ack.side_effect = (
            FinalDeliveryBarrierPending(
                "events pending"
            )
        )

        first = self.orchestrator.process_command(
            self.command
        )

        self.assertFalse(first.success)

        self.reader_executor.stop.assert_called_once_with(
            session_key="session-1",
            reader_code="receiving-door-01",
        )

        self.before_session_close.assert_called_once_with(
            session_key="session-1",
            reader_code="receiving-door-01",
        )

        mocked_close_local_session.assert_called_once_with(
            session_key="session-1",
            reader_code="receiving-door-01",
        )

        self.api_client.ack.assert_not_called()

        self.assertEqual(
            self.orchestrator.runtime.state,
            RuntimeState.IDLE,
        )

        self.assertEqual(
            self.orchestrator.runtime.completed_command,
            RuntimeCommand.STOP,
        )

        self.before_success_ack.side_effect = None

        second = self.orchestrator.process_command(
            self.command
        )

        self.assertTrue(second.success)

        self.reader_executor.stop.assert_called_once()
        self.before_session_close.assert_called_once()
        mocked_close_local_session.assert_called_once()

        self.assertEqual(
            self.before_success_ack.call_count,
            2,
        )

        self.api_client.ack.assert_called_once_with(
            session_key="session-1",
            reader_code="receiving-door-01",
            command="stop",
            revision=2,
            success=True,
            message="Reader stopped successfully.",
        )
