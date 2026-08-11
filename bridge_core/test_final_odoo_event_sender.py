import uuid

from django.test import TestCase

from bridge_core.final_odoo_event_sender import (
    FinalOdooEventSender,
    FinalOdooEventSenderError,
)
from bridge_core.models import (
    DeliveryAttempt,
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)
from bridge_core.odoo_api_v1 import (
    OdooRFIDApiTransportError,
)


class FakeApiClient:
    def __init__(self):
        self.calls = []
        self.result = None
        self.error = None

    def events(
        self,
        *,
        session_key,
        reader_code,
        events,
    ):
        self.calls.append(
            {
                "session_key": session_key,
                "reader_code": reader_code,
                "events": list(events),
            }
        )

        if self.error is not None:
            raise self.error

        return self.result


class FinalOdooEventSenderTests(TestCase):
    def setUp(self):
        self.reader = ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            enabled=True,
        )

        self.session = RFIDSession.objects.create(
            external_session_key="session-001",
            device=self.reader,
            operation_type=(
                RFIDSession.OperationType.RECEIPT
            ),
            odoo_record_id=0,
            odoo_reference="EXWS1/IN/02227",
        )

        self.event = RawRFIDEvent.objects.create(
            event_id=uuid.uuid4(),
            device=self.reader,
            rfid_session=self.session,
            reader_event_key=(
                "final:sessionhash:epchash"
            ),
            epc="E2000017221101441890ABCD",
            raw_payload="{}",
            queue_state=(
                RawRFIDEvent.QueueState.QUEUED
            ),
        )

        self.api = FakeApiClient()

        self.sender = FinalOdooEventSender(
            api_client=self.api,
            reader_code=self.reader.code,
        )

    def response(self, outcome):
        return {
            "ok": True,
            "state": "active",
            "results": [
                {
                    "event_uuid": str(
                        self.event.event_id
                    ),
                    "epc": self.event.epc,
                    "outcome": outcome,
                    "classification": "matched",
                }
            ],
        }

    def test_accepted_event_is_sent(self):
        self.api.result = self.response(
            "accepted"
        )

        result = self.sender.send_event(
            event=self.event
        )

        self.assertEqual(
            result.outcome,
            DeliveryAttempt.Outcome.SENT,
        )

        call = self.api.calls[0]

        self.assertEqual(
            call["session_key"],
            "session-001",
        )

        self.assertEqual(
            call["events"][0]["event_uuid"],
            str(self.event.event_id),
        )

        self.assertEqual(
            call["events"][0]["raw_read_count"],
            1,
        )

    def test_duplicate_event_is_success(self):
        self.api.result = self.response(
            "duplicate"
        )

        result = self.sender.send_event(
            event=self.event
        )

        self.assertEqual(
            result.outcome,
            DeliveryAttempt.Outcome.SENT,
        )

    def test_transport_error_retries(self):
        self.api.error = (
            OdooRFIDApiTransportError(
                "network unavailable"
            )
        )

        result = self.sender.send_event(
            event=self.event
        )

        self.assertEqual(
            result.outcome,
            DeliveryAttempt.Outcome.RETRY,
        )

    def test_unmatched_response_retries(self):
        self.api.result = {
            "ok": True,
            "results": [
                {
                    "event_uuid": str(
                        uuid.uuid4()
                    ),
                    "outcome": "accepted",
                }
            ],
        }

        result = self.sender.send_event(
            event=self.event
        )

        self.assertEqual(
            result.outcome,
            DeliveryAttempt.Outcome.RETRY,
        )

    def test_rejected_event_is_rejected(self):
        self.api.result = self.response(
            "rejected"
        )

        result = self.sender.send_event(
            event=self.event
        )

        self.assertEqual(
            result.outcome,
            DeliveryAttempt.Outcome.REJECTED,
        )

    def test_historical_event_is_refused(self):
        self.event.reader_event_key = (
            "legacy-reader-event"
        )

        with self.assertRaises(
            FinalOdooEventSenderError
        ):
            self.sender.send_event(
                event=self.event
            )
