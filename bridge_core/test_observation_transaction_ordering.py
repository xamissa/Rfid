"""Observation-first Odoo ordering, with no HTTP, reader or deployment DB."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bridge_core import test_observations as fixtures
from bridge_core.final_delivery_cycle import run_final_delivery_cycle
from bridge_core.final_odoo_event_sender import FinalOdooEventSender
from bridge_core.final_runtime_state import RuntimeState
from bridge_core.final_tag_ingestion import FinalTagIngestionError, ingest_final_active_tag_frames
from bridge_core.final_worker_cycle import FinalWorkerCycle
from bridge_core.models import DeliveryAttempt, ObservationOutbox, RawRFIDEvent, RFIDSession
from bridge_core.observation_delivery import ObservationDeliveryWorker
from bridge_core.odoo_api_v1 import OdooRFIDApiTransportError


class ObservationFirstServer:
    gateway_code = 'offline-gateway'

    def __init__(self, lose_event_response=False):
        self.linked = set()
        self.events_seen = set()
        self.outcomes = []
        self.lose_event_response = lose_event_response

    def observations(self, *, reader_code, observations):
        for item in observations:
            self.linked.add((item['session_key'], item['epc']))
        return {'ok': True, 'results': [dict(event_uuid=item['event_uuid'], status='accepted')
                                      for item in observations]}

    def events(self, *, session_key, reader_code, events, observation_ledger_managed):
        assert observation_ledger_managed is True
        results = []
        for event in events:
            assert (session_key, event['epc']) in self.linked
            identity = event['event_uuid']
            if self.lose_event_response and identity not in self.events_seen:
                self.events_seen.add(identity)
                raise OdooRFIDApiTransportError('synthetic lost response')
            outcome = 'duplicate_event' if identity in self.events_seen else 'duplicate_epc'
            self.events_seen.add(identity)
            self.outcomes.append(outcome)
            results.append(dict(event_uuid=identity, epc=event['epc'], outcome=outcome))
        return {'ok': True, 'results': results}


class ObservationTransactionOrderingTests(TestCase):
    def setUp(self):
        fixtures.PipelineIntegrationTests.setUp(self)
        self.worker = FinalWorkerCycle(orchestrator=self.runtime, reader_executor=self.reader,
            device=self.device, capture_service=self.reader)
        self.runtime.before_session_close = self.worker.drain_pending_tags
        self.runtime.before_success_ack = self.worker.require_stop_delivery_complete
        self.runtime.api_client.commands.return_value = []
        self.server = ObservationFirstServer()
        self.sender = FinalOdooEventSender(api_client=self.server, reader_code=self.device.code)

    emit = fixtures.PipelineIntegrationTests.emit

    def poll(self, action, key='first', revision=None):
        self.runtime.api_client.commands.return_value = [dict(
            command=action, session_key=key, reader_code=self.device.code,
            operation='receipt', picking='OFFLINE/IN/001',
            revision=revision or {'start': 1, 'stop': 2, 'abort': 3}[action])]
        self.worker.run_once()
        self.runtime.api_client.commands.return_value = []

    def observed_first(self):
        self.poll('start')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.READING)
        self.emit([fixtures.tag('E28011B0A503007D6BAF049D'), fixtures.tag('E28011B0A503007D6BAF04AD')])
        self.tick += 1.1
        self.reader.step()  # inactivity finalizes observations without closing the business window
        ObservationOutbox.objects.update(created_at=timezone.now()-timedelta(seconds=2))
        delivery = ObservationDeliveryWorker(api_client=self.server, reader_code=self.device.code,
                                             config=self.config, jitter=lambda a, b: 1)
        self.assertEqual(delivery.run_once(), 2)
        self.assertEqual(ObservationOutbox.objects.filter(state='delivered').count(), 2)
        self.assertEqual(RawRFIDEvent.objects.filter(queue_state='queued').count(), 2)

    def deliver(self):
        return run_final_delivery_cycle(sender=self.sender, reader_code=self.device.code,
            batch_size=100, max_delivery_attempts=10, retry_initial_seconds=1, retry_max_seconds=30)

    def assert_can_finish_and_restart(self):
        self.assertEqual(RawRFIDEvent.objects.filter(queue_state='sent').count(), 2)
        self.emit([fixtures.tag('E28011B0A503007D6BAF049D')])
        self.assertIsNone(self.reader.window_error)
        self.assertEqual(RawRFIDEvent.objects.count(), 2)
        self.poll('stop')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.IDLE)
        self.assertTrue(self.runtime.api_client.ack.call_args.kwargs['success'])
        self.assertEqual(RFIDSession.objects.get(external_session_key='first').status, 'closed')
        self.poll('start', key='second')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.READING)
        self.assertEqual(self.reader.active_session_key, 'second')
        self.assertEqual(self.physical.starts, 1)
        self.assertEqual(self.physical.stops, 0)

    def test_observations_first_duplicate_epc_sent_stop_and_next_start(self):
        self.observed_first()
        result = self.deliver()
        self.assertEqual(result.sent_count, 2)
        self.assertEqual(self.server.outcomes, ['duplicate_epc', 'duplicate_epc'])
        self.assert_can_finish_and_restart()

    def test_lost_events_response_duplicate_event_sent_stop_and_next_start(self):
        self.server.lose_event_response = True
        self.observed_first()
        self.assertEqual(self.deliver().retry_count, 2)
        identities = list(RawRFIDEvent.objects.order_by('id').values_list('event_id', flat=True))
        DeliveryAttempt.objects.filter(outcome='retry').update(next_retry_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.deliver().sent_count, 2)
        self.assertEqual(list(RawRFIDEvent.objects.order_by('id').values_list('event_id', flat=True)), identities)
        self.assertEqual(self.server.outcomes, ['duplicate_event', 'duplicate_event'])
        self.assert_can_finish_and_restart()

    def test_old_sender_rejection_poison_requires_local_abort_not_file_edit(self):
        self.observed_first()
        with patch.object(FinalOdooEventSender, 'ACCEPTED_OUTCOMES', {'accepted', 'duplicate'}):
            self.assertEqual(self.deliver().rejected_count, 2)
        with self.assertRaisesRegex(FinalTagIngestionError, 'invalid final-runtime queue state: rejected'):
            ingest_final_active_tag_frames(device=self.device, session_key='first',
                                           frames=[fixtures.tag('E28011B0A503007D6BAF049D')])
        self.emit([fixtures.tag('E28011B0A503007D6BAF049D')])
        self.assertIsNotNone(self.reader.window_error)
        self.reader.step()  # physical reconnection must not heal the interrupted window
        self.worker.run_once()
        self.assertEqual(self.runtime.runtime.state, RuntimeState.DEGRADED)
        self.assertEqual(self.runtime.runtime.session_key, 'first')
        self.assertEqual(self.deliver().selected_count, 0)  # sender fix never rewrites terminal history
        self.poll('start', key='second')
        self.assertFalse(self.runtime.api_client.ack.call_args.kwargs['success'])
        self.poll('abort')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.IDLE)
        self.assertIsNone(self.reader.window_error)
        self.assertEqual(RFIDSession.objects.get(external_session_key='first').status, 'cancelled')
        self.assertEqual(RawRFIDEvent.objects.filter(queue_state='rejected').count(), 2)
        self.poll('start', key='second')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.READING)

    def test_start_after_transient_empty_runtime_health_failure_in_same_cycle(self):
        # Busy during the cycle health check, healthy again after HTTP polling.
        # Baseline rejects START with the exact live "state is degraded" message.
        from bridge_core.final_reader_supervisor import ReaderSupervisorError
        original = self.reader.require_healthy
        checks = 0
        def temporarily_busy():
            nonlocal checks
            checks += 1
            if checks == 1:
                raise ReaderSupervisorError('Physical reader busy; health cannot be verified.')
            return original()
        with patch.object(self.reader, 'require_healthy', side_effect=temporarily_busy):
            self.poll('start')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.READING)
        self.assertEqual(self.reader.active_session_key, 'first')
        self.assertTrue(self.runtime.api_client.ack.call_args.kwargs['success'])

    def test_healthy_hardware_cannot_clear_degraded_business_session(self):
        self.poll('start')
        self.runtime.runtime = self.runtime.runtime.mark_degraded('business integrity conflict')
        self.poll('start', key='second')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.DEGRADED)
        self.assertEqual(self.runtime.runtime.session_key, 'first')
        self.assertEqual(self.runtime.api_client.ack.call_args.kwargs['message'],
                         'START is invalid while reader state is degraded.')
        self.assertEqual(RFIDSession.objects.count(), 1)

    def test_unhealthy_hardware_cannot_clear_empty_degraded_runtime(self):
        from bridge_core.final_reader_supervisor import HardwareState
        self.reader.state = HardwareState.RECONNECTING
        self.poll('start')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.DEGRADED)
        self.assertFalse(self.runtime.api_client.ack.call_args.kwargs['success'])
        self.assertEqual(RFIDSession.objects.count(), 0)

    def test_repeated_epc_is_safe_while_delivery_is_pending(self):
        self.observed_first()
        for state in ('queued', 'inflight', 'retry', 'sent'):
            with self.subTest(state=state):
                RawRFIDEvent.objects.update(queue_state=state)
                result = ingest_final_active_tag_frames(device=self.device, session_key='first',
                    frames=[fixtures.tag('E28011B0A503007D6BAF049D')])
                self.assertEqual(result.created_count, 0)
                self.assertEqual(RawRFIDEvent.objects.count(), 2)
        for state in ('rejected', 'dead'):
            with self.subTest(state=state):
                RawRFIDEvent.objects.update(queue_state=state)
                with self.assertRaises(FinalTagIngestionError):
                    ingest_final_active_tag_frames(device=self.device, session_key='first',
                        frames=[fixtures.tag('E28011B0A503007D6BAF049D')])

    def test_empty_runtime_cannot_recover_with_a_still_attached_window(self):
        from bridge_core.final_runtime_state import LocalReaderRuntime
        self.poll('start')
        self.runtime.runtime = LocalReaderRuntime(reader_code=self.device.code,
            state=RuntimeState.DEGRADED, session_key=None, error='unresolved business context')
        self.poll('start', key='second')
        self.assertEqual(self.runtime.runtime.state, RuntimeState.DEGRADED)
        self.assertEqual(self.reader.active_session_key, 'first')
        self.assertEqual(RFIDSession.objects.count(), 1)
        self.assertFalse(self.runtime.api_client.ack.call_args.kwargs['success'])
