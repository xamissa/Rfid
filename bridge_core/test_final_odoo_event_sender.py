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
        observation_ledger_managed=None,
    ):
        self.calls.append(
            {
                "session_key": session_key,
                "reader_code": reader_code,
                "events": list(events),
                "observation_ledger_managed": observation_ledger_managed,
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
        self.assertIs(call["observation_ledger_managed"], True)

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

    def test_final_sender_serializes_real_request_boolean(self):
        import json
        from bridge_core.odoo_api_v1 import OdooRFIDApiClient
        from bridge_core.test_odoo_api_v1 import RecordingOpenerFactory
        factory = RecordingOpenerFactory({'result': self.response('accepted')})
        client = OdooRFIDApiClient(base_url='https://offline.invalid',
            bearer_token='offline-placeholder', gateway_code='gateway', opener_factory=factory)
        sender = FinalOdooEventSender(api_client=client, reader_code=self.reader.code)
        result = sender.send_event(event=self.event)
        self.assertEqual(result.outcome, DeliveryAttempt.Outcome.SENT)
        request = factory.opener.requests[0]['request']
        params = json.loads(request.data)['params']
        self.assertIs(params['observation_ledger_managed'], True)
        self.assertIn(b'"observation_ledger_managed":true', request.data)
        self.assertNotIn('observation_ledger_managed', params['events'][0])
        self.assertEqual(params['events'][0]['event_uuid'], str(self.event.event_id))
        self.assertEqual(params['events'][0]['raw_read_count'], 1)

    def test_observation_first_idempotent_outcomes_are_normalized_success(self):
        for outcome in ('duplicate_epc', 'duplicate_event', ' DUPLICATE_EPC ', 'DuPlIcAtE_EvEnT'):
            with self.subTest(outcome=outcome):
                self.api.result = self.response(outcome)
                result = self.sender.send_event(event=self.event)
                self.assertEqual(result.outcome, DeliveryAttempt.Outcome.SENT)
                self.assertEqual(result.response_code, outcome.strip().lower())

    def test_genuine_rejections_are_not_idempotent_success(self):
        for outcome in ('rejected', 'unknown_session', 'wrong_reader', 'invalid_payload',
                        'conflict', 'integrity_conflict', 'unexpected_outcome'):
            with self.subTest(outcome=outcome):
                self.api.result = self.response(outcome)
                self.assertEqual(self.sender.send_event(event=self.event).outcome,
                                 DeliveryAttempt.Outcome.REJECTED)

    def test_duplicate_success_still_requires_unique_correlated_uuid(self):
        for outcome in ('duplicate_epc', 'duplicate_event'):
            with self.subTest(outcome=outcome):
                self.api.result = self.response(outcome)
                self.api.result['results'][0]['event_uuid'] = str(uuid.uuid4())
                self.assertEqual(self.sender.send_event(event=self.event).outcome,
                                 DeliveryAttempt.Outcome.RETRY)
                self.api.result = self.response(outcome)
                self.api.result['results'] *= 2
                self.assertEqual(self.sender.send_event(event=self.event).outcome,
                                 DeliveryAttempt.Outcome.RETRY)

    def test_wrong_reader_and_invalid_session_still_fail_before_sending(self):
        self.sender.reader_code = 'wrong-reader'
        with self.assertRaises(FinalOdooEventSenderError):
            self.sender.send_event(event=self.event)
        self.sender.reader_code = self.reader.code
        self.event.rfid_session.external_session_key = ''
        with self.assertRaises(FinalOdooEventSenderError):
            self.sender.send_event(event=self.event)
        self.assertEqual(self.api.calls, [])
