from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from bridge_core.final_delivery_cycle import (
    run_final_delivery_cycle,
    select_final_delivery_candidates,
)
from bridge_core.models import (
    DeliveryAttempt,
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)
from bridge_core.sender_backends import (
    DeliverySenderResult,
)


class SentSender:
    def send_event(self, *, event):
        del event

        return DeliverySenderResult(
            outcome=DeliveryAttempt.Outcome.SENT,
            response_code="accepted",
        )


class FinalDeliverySelectionTests(TestCase):
    def setUp(self):
        self.final_reader = (
            ReaderDevice.objects.create(
                code="receiving-door-01",
                name="Receiving Door 1",
                role=ReaderDevice.Role.RECEIVING,
                enabled=True,
            )
        )

        self.legacy_reader = (
            ReaderDevice.objects.create(
                code="door1",
                name="Historical Door 1",
                role=ReaderDevice.Role.RECEIVING,
                enabled=False,
            )
        )

        self.final_session = (
            RFIDSession.objects.create(
                external_session_key="session-final",
                device=self.final_reader,
                operation_type=(
                    RFIDSession.OperationType.RECEIPT
                ),
                odoo_record_id=0,
            )
        )

        self.legacy_session = (
            RFIDSession.objects.create(
                external_session_key="session-old",
                device=self.legacy_reader,
                operation_type=(
                    RFIDSession.OperationType.RECEIPT
                ),
                odoo_record_id=0,
            )
        )

    def create_event(
        self,
        *,
        reader,
        session,
        key,
        state=RawRFIDEvent.QueueState.QUEUED,
    ):
        return RawRFIDEvent.objects.create(
            device=reader,
            rfid_session=session,
            reader_event_key=key,
            epc="E2000017221101441890ABCD",
            raw_payload="{}",
            queue_state=state,
        )

    def test_only_final_reader_final_keys_selected(self):
        final_event = self.create_event(
            reader=self.final_reader,
            session=self.final_session,
            key="final:a:b",
        )

        self.create_event(
            reader=self.legacy_reader,
            session=self.legacy_session,
            key="final:old:event",
        )

        self.create_event(
            reader=self.final_reader,
            session=self.final_session,
            key="legacy-event",
        )

        selected = (
            select_final_delivery_candidates(
                reader_code="receiving-door-01",
                batch_size=50,
            )
        )

        self.assertEqual(
            [event.id for event in selected],
            [final_event.id],
        )

    def test_unassigned_event_is_not_selected(self):
        self.create_event(
            reader=self.final_reader,
            session=None,
            key="final:a:b",
        )

        selected = (
            select_final_delivery_candidates(
                reader_code="receiving-door-01",
                batch_size=50,
            )
        )

        self.assertEqual(
            selected,
            (),
        )

    def test_future_retry_is_not_selected(self):
        event = self.create_event(
            reader=self.final_reader,
            session=self.final_session,
            key="final:a:b",
            state=RawRFIDEvent.QueueState.RETRY,
        )

        DeliveryAttempt.objects.create(
            event=event,
            attempt_number=1,
            outcome=DeliveryAttempt.Outcome.RETRY,
            completed_at=timezone.now(),
            next_retry_at=(
                timezone.now()
                + timedelta(minutes=10)
            ),
        )

        selected = (
            select_final_delivery_candidates(
                reader_code="receiving-door-01",
                batch_size=50,
            )
        )

        self.assertEqual(
            selected,
            (),
        )

    def test_batch_uses_existing_delivery_attempt_state_machine(self):
        self.create_event(
            reader=self.final_reader,
            session=self.final_session,
            key="final:a:b",
        )

        result = run_final_delivery_cycle(
            sender=SentSender(),
            reader_code="receiving-door-01",
            batch_size=50,
            max_delivery_attempts=10,
            retry_initial_seconds=30,
            retry_max_seconds=3600,
        )

        self.assertEqual(
            result.selected_count,
            1,
        )
        self.assertEqual(
            result.sent_count,
            1,
        )

        event = RawRFIDEvent.objects.get(
            device=self.final_reader,
        )

        self.assertEqual(
            event.queue_state,
            RawRFIDEvent.QueueState.SENT,
        )

        self.assertEqual(
            event.delivery_attempts.count(),
            1,
        )


class FinalDeliveryDeadLetterTests(TestCase):
    def setUp(self):
        self.reader = ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            enabled=True,
        )

        self.session = RFIDSession.objects.create(
            external_session_key="session-dead",
            device=self.reader,
            operation_type=(
                RFIDSession.OperationType.RECEIPT
            ),
            odoo_record_id=0,
        )

    def make_retry_event(self):
        return RawRFIDEvent.objects.create(
            device=self.reader,
            rfid_session=self.session,
            reader_event_key="final:dead:test",
            epc="E2000017221101441890ABCD",
            raw_payload="{}",
            queue_state=(
                RawRFIDEvent.QueueState.RETRY
            ),
        )

    def test_exhausted_retry_is_dead_lettered(self):
        from bridge_core.final_delivery_cycle import (
            dead_letter_exhausted_final_events,
        )

        event = self.make_retry_event()

        for number in range(1, 4):
            DeliveryAttempt.objects.create(
                event=event,
                attempt_number=number,
                outcome=DeliveryAttempt.Outcome.RETRY,
                completed_at=timezone.now(),
                next_retry_at=timezone.now(),
            )

        count = (
            dead_letter_exhausted_final_events(
                reader_code="receiving-door-01",
                max_delivery_attempts=3,
            )
        )

        self.assertEqual(
            count,
            1,
        )

        event.refresh_from_db()

        self.assertEqual(
            event.queue_state,
            RawRFIDEvent.QueueState.DEAD,
        )

    def test_nonexhausted_retry_remains_retry(self):
        from bridge_core.final_delivery_cycle import (
            dead_letter_exhausted_final_events,
        )

        event = self.make_retry_event()

        DeliveryAttempt.objects.create(
            event=event,
            attempt_number=1,
            outcome=DeliveryAttempt.Outcome.RETRY,
            completed_at=timezone.now(),
            next_retry_at=timezone.now(),
        )

        count = (
            dead_letter_exhausted_final_events(
                reader_code="receiving-door-01",
                max_delivery_attempts=3,
            )
        )

        self.assertEqual(
            count,
            0,
        )

        event.refresh_from_db()

        self.assertEqual(
            event.queue_state,
            RawRFIDEvent.QueueState.RETRY,
        )
