from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase

from bridge_core.final_delivery_cycle import (
    dead_letter_cancelled_session_final_events,
)
from bridge_core.models import RawRFIDEvent


class CancelledSessionFinalDeliveryTests(SimpleTestCase):
    @patch(
        "bridge_core.final_delivery_cycle."
        "transition_event_queue_state"
    )
    @patch(
        "bridge_core.final_delivery_cycle."
        "RawRFIDEvent.objects"
    )
    def test_cancelled_queued_and_retry_events_become_dead(
        self,
        mocked_objects,
        mocked_transition,
    ):
        queryset = Mock()
        mocked_objects.filter.return_value = queryset

        queryset.values_list.return_value = (
            ("event-queued", RawRFIDEvent.QueueState.QUEUED),
            ("event-retry", RawRFIDEvent.QueueState.RETRY),
        )

        result = dead_letter_cancelled_session_final_events(
            reader_code="receiving-door-01",
        )

        mocked_objects.filter.assert_called_once()

        filter_kwargs = mocked_objects.filter.call_args.kwargs

        self.assertEqual(
            filter_kwargs["device__code"],
            "receiving-door-01",
        )
        self.assertEqual(
            filter_kwargs["rfid_session__status"],
            "cancelled",
        )
        self.assertEqual(
            set(filter_kwargs["queue_state__in"]),
            {"queued", "retry"},
        )
        self.assertEqual(
            filter_kwargs["reader_event_key__startswith"],
            "final:",
        )

        self.assertEqual(
            mocked_transition.call_args_list,
            [
                call(
                    event_id="event-queued",
                    expected_state="queued",
                    target_state="dead",
                ),
                call(
                    event_id="event-retry",
                    expected_state="retry",
                    target_state="dead",
                ),
            ],
        )

        self.assertEqual(result, 2)

    @patch(
        "bridge_core.final_delivery_cycle."
        "transition_event_queue_state"
    )
    @patch(
        "bridge_core.final_delivery_cycle."
        "RawRFIDEvent.objects"
    )
    def test_no_cancelled_pending_events_changes_nothing(
        self,
        mocked_objects,
        mocked_transition,
    ):
        queryset = Mock()
        mocked_objects.filter.return_value = queryset
        queryset.values_list.return_value = ()

        result = dead_letter_cancelled_session_final_events(
            reader_code="receiving-door-01",
        )

        mocked_transition.assert_not_called()
        self.assertEqual(result, 0)

    def test_empty_reader_code_fails_closed(self):
        with self.assertRaisesMessage(
            RuntimeError,
            "Final RFID reader code cannot be empty.",
        ):
            dead_letter_cancelled_session_final_events(
                reader_code="",
            )
