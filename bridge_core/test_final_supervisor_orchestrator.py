"""Offline integration of continuous inventory, business windows and ACK replay."""
from unittest.mock import Mock, patch

from django.test import TestCase

from bridge_core.final_reader_supervisor import ContinuousReaderSupervisor, HardwareState
from bridge_core.final_runtime_orchestrator import FinalRuntimeOrchestrator
from bridge_core.final_runtime_state import OdooRFIDCommand, RuntimeState
from bridge_core.final_tag_ingestion import ingest_final_active_tag_frames
from bridge_core.final_worker_cycle import FinalWorkerCycle
from bridge_core.models import ReaderDevice, RFIDSession, RawRFIDEvent
from bridge_core.test_final_tag_ingestion import active_tag_frame


class SupervisorOrchestratorTests(TestCase):
    def setUp(self):
        self.device = ReaderDevice.objects.create(
            code='receiving-door-01', name='Shared reader', host='offline.invalid',
            role=ReaderDevice.Role.RECEIVING, device_address=2, enabled=True,
            inventory_mode=ReaderDevice.InventoryMode.ACTIVE, shared_operations=True)
        self.physical = Mock()
        self.physical.has_partial_frame = False
        self.physical.poll_tag_frames.return_value = ()
        self.reader = ContinuousReaderSupervisor(
            physical=self.physical, device=self.device,
            tag_ingestor=ingest_final_active_tag_frames)
        self.reader.step()
        self.api = Mock()
        self.api.ack.return_value = {'ok': True}
        self.orchestrator = FinalRuntimeOrchestrator(
            api_client=self.api, reader_executor=self.reader)
        self.worker = FinalWorkerCycle(
            orchestrator=self.orchestrator, reader_executor=self.reader,
            device=self.device, capture_service=self.reader)
        self.orchestrator.before_session_close = self.worker.drain_pending_tags
        self.orchestrator.before_success_ack = self.worker.require_stop_delivery_complete
        self.orchestrator.mark_reader_verified_idle()

    def command(self, name, key='one', operation='receipt'):
        return OdooRFIDCommand.from_payload(dict(
            session_key=key, reader_code=self.device.code, command=name,
            revision={'start': 1, 'stop': 2, 'abort': 3}[name],
            picking='WH/IN/001', operation=operation))

    def process(self, name, **kwargs):
        return self.orchestrator.process_command(self.command(name, **kwargs))

    def emit(self, epc):
        self.physical.poll_tag_frames.return_value = (active_tag_frame(epc),)
        self.reader.step()
        self.physical.poll_tag_frames.return_value = ()

    def test_start_ack_retry_only_replays_ack(self):
        self.api.ack.side_effect = [OSError('Odoo unavailable'), {'ok': True}]
        with patch.object(self.reader, 'start', wraps=self.reader.start) as attach:
            self.assertFalse(self.process('start').success)
            self.assertEqual(self.orchestrator.runtime.state, RuntimeState.READING)
            self.emit('AABB')
            # If retry attempted admission it would consume these stale frames.
            self.physical.poll_tag_frames.return_value = (active_tag_frame('CCDD'),)
            reads = self.physical.poll_tag_frames.call_count
            self.assertTrue(self.process('start').success)
            self.assertEqual(self.physical.poll_tag_frames.call_count, reads)
            attach.assert_called_once()
        self.assertEqual(RFIDSession.objects.count(), 1)
        self.assertEqual(RawRFIDEvent.objects.count(), 1)
        self.physical.start.assert_called_once()
        self.assertEqual(self.reader.state, HardwareState.ACTIVE)

    def test_stop_ack_retry_finalises_and_detaches_once(self):
        self.assertTrue(self.process('start').success)
        self.emit('AABB')  # persistence completes immediately before STOP
        RawRFIDEvent.objects.update(queue_state=RawRFIDEvent.QueueState.SENT)
        self.api.ack.reset_mock()
        self.api.ack.side_effect = [OSError('ACK lost'), {'ok': True}]
        with patch.object(self.reader, 'stop', wraps=self.reader.stop) as detach:
            self.assertFalse(self.process('stop').success)
            closed = RFIDSession.objects.get()
            self.assertEqual(closed.status, RFIDSession.Status.CLOSED)
            self.assertEqual(self.orchestrator.runtime.state, RuntimeState.IDLE)
            closed_at = closed.closed_at
            self.emit('CCDD')  # immediately after detach is ambient
            self.assertTrue(self.process('stop').success)
            closed.refresh_from_db()
            self.assertEqual(closed.closed_at, closed_at)
            detach.assert_called_once()
        self.assertEqual(RawRFIDEvent.objects.count(), 1)
        self.physical.stop.assert_not_called()
        self.physical.start.assert_called_once()

    def test_delivery_barrier_pending_failed_then_sent_with_supervisor(self):
        self.assertTrue(self.process('start').success)
        self.emit('AABB')
        self.api.ack.reset_mock()
        with patch.object(self.reader, 'stop', wraps=self.reader.stop) as detach:
            self.assertFalse(self.process('stop').success)
            self.assertEqual(RFIDSession.objects.get().status, RFIDSession.Status.CLOSED)
            self.assertFalse(self.reader.is_active)
            self.api.ack.assert_not_called()
            for state in (RawRFIDEvent.QueueState.RETRY, RawRFIDEvent.QueueState.INFLIGHT,
                          RawRFIDEvent.QueueState.REJECTED, RawRFIDEvent.QueueState.DEAD):
                RawRFIDEvent.objects.update(queue_state=state)
                self.assertFalse(self.process('stop').success)
                self.api.ack.assert_not_called()
            RawRFIDEvent.objects.update(queue_state=RawRFIDEvent.QueueState.SENT)
            self.assertTrue(self.process('stop').success)
            detach.assert_called_once()
        self.physical.stop.assert_not_called()

    def fail_hardware(self):
        self.physical.poll_tag_frames.side_effect = OSError('reader lost')
        self.assertFalse(self.reader.step())
        self.physical.poll_tag_frames.side_effect = None

    def test_failure_before_start_ack_blocks_success_and_requires_abort(self):
        # Fault after attachment/completion but before the final health check.
        original = self.reader.require_healthy
        def check():
            if self.reader.is_active:
                self.fail_hardware()
            return original()
        with patch.object(self.reader, 'require_healthy', side_effect=check):
            self.assertFalse(self.process('start').success)
        self.api.ack.assert_not_called()
        self.assertIsNotNone(self.reader.window_error)
        self.emit('AABB')  # reconnect observation is suppressed
        self.assertEqual(self.reader.state, HardwareState.ACTIVE)
        self.assertFalse(self.process('start').success)
        self.assertFalse(self.api.ack.call_args.kwargs['success'])
        self.assertFalse(self.process('stop').success)
        self.assertEqual(RawRFIDEvent.objects.count(), 0)
        self.assertTrue(self.process('abort').success)
        self.assertIsNone(self.reader.window_error)
        self.assertFalse(self.reader.is_active)
        self.assertEqual(RFIDSession.objects.get().status, RFIDSession.Status.CANCELLED)
        self.physical.stop.assert_not_called()
        self.assertTrue(self.process('start', key='two').success)

    def test_failure_during_stop_barrier_blocks_success_until_reader_recovers(self):
        self.assertTrue(self.process('start').success)
        self.api.ack.reset_mock()
        barrier = self.orchestrator.before_success_ack
        def failing_barrier(**kwargs):
            barrier(**kwargs)
            self.fail_hardware()
        self.orchestrator.before_success_ack = failing_barrier
        self.assertFalse(self.process('stop').success)
        self.api.ack.assert_not_called()
        self.assertEqual(RFIDSession.objects.get().status, RFIDSession.Status.CLOSED)
        self.reader.step()
        self.orchestrator.before_success_ack = barrier
        self.assertTrue(self.process('stop').success)
        self.physical.stop.assert_not_called()

    def test_ambient_boundary_and_next_dispatch_session(self):
        self.emit('AA01')  # immediately before START
        self.physical.poll_tag_frames.side_effect = [
            (active_tag_frame('AA02'),), (active_tag_frame('AA03'),), ()]
        self.assertTrue(self.process('start').success)
        self.physical.poll_tag_frames.side_effect = None
        self.emit('AA04')
        self.emit('AA04')  # deterministic session+EPC idempotency
        RawRFIDEvent.objects.update(queue_state=RawRFIDEvent.QueueState.SENT)
        self.assertTrue(self.process('stop').success)
        self.emit('AA05')
        self.physical.poll_tag_frames.side_effect = [(active_tag_frame('AA06'),), ()]
        self.assertTrue(self.process('start', key='two', operation='dispatch').success)
        self.physical.poll_tag_frames.side_effect = None
        self.emit('AA07')
        self.assertEqual(list(RawRFIDEvent.objects.order_by('epc').values_list(
            'epc', 'rfid_session__external_session_key')), [('AA04', 'one'), ('AA07', 'two')])
        self.physical.start.assert_called_once()
        self.physical.stop.assert_not_called()
