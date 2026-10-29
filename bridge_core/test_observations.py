"""Offline durable capture, context, leases and real batch-delivery tests."""
from datetime import timedelta
import struct
from types import SimpleNamespace
import uuid
from unittest.mock import Mock, patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from bridge_core.models import ReaderDevice, RFIDObservation, ObservationOutbox, ObservationDeliveryAttempt, RawRFIDEvent
from bridge_core.observation_capture import ObservationCapture, observation_metrics
from bridge_core.observation_config import ObservationConfig
from bridge_core.observation_delivery import ObservationDeliveryWorker, encoded_size
from bridge_core.odoo_api_v1 import OdooRFIDApiTransportError
from bridge_core.reader_protocol import RFIDFrame, COMMAND_ACTIVE_TAG, COMMAND_START_INVENTORY, COMMAND_STOP
from bridge_core.final_reader_executor import PersistentActiveReaderExecutor
from bridge_core.final_reader_supervisor import ContinuousReaderSupervisor
from bridge_core.final_runtime_orchestrator import FinalRuntimeOrchestrator
from bridge_core.final_runtime_state import OdooRFIDCommand
from bridge_core.final_tag_ingestion import ingest_final_active_tag_frames
from bridge_core.test_final_reader_executor import FakeSession, RecordingTransportFactory, frame as control_frame


def tag(epc='AABB', antenna=None, count=999):
    raw = bytes.fromhex(epc)
    mask = (1 << 2) | (1 << 4) | (1 if antenna is not None else 0)
    payload = struct.pack('>IB', mask, 1)
    if antenna is not None:
        payload += bytes([antenna])
    payload += bytes([len(raw)]) + raw + struct.pack('>i', count)
    return RFIDFrame(2, 1, COMMAND_ACTIVE_TAG, 0, payload, b'')


class ObservationTests(TestCase):
    def setUp(self):
        self.device = ReaderDevice.objects.create(code='receiving-door-01', name='Offline',
            host='offline.invalid', role='receiving', device_address=2,
            inventory_mode='active', enabled=True, shared_operations=True)
        self.tick = 1.0
        self.config = ObservationConfig(batch_flush_seconds=0.01)
        self.capture = ObservationCapture(device=self.device, config=self.config, monotonic=lambda: self.tick)
        self.api = Mock(gateway_code='gateway')
        self.api.observations.side_effect = lambda **kw: {'ok': True, 'results': [
            {'event_uuid': item['event_uuid'], 'status': 'accepted'} for item in kw['observations']]}
        self.worker = ObservationDeliveryWorker(api_client=self.api, reader_code=self.device.code,
            config=self.config, jitter=lambda a, b: 1)

    def finalize(self, frames=None):
        self.capture.ingest(frames or [tag()])
        self.capture.boundary('stop')
        ObservationOutbox.objects.update(created_at=timezone.now()-timedelta(seconds=2))

    def due(self):
        ObservationOutbox.objects.filter(state='pending').update(next_attempt_at=timezone.now()-timedelta(seconds=1))

    def test_ambient_durable_and_reader_count_not_used(self):
        self.capture.ingest([tag(), tag(), tag(antenna=0), tag(antenna=2)])
        rows = list(RFIDObservation.objects.order_by('antenna_key'))
        self.assertEqual([(r.antenna, r.raw_read_count) for r in rows], [(None, 2), (0, 1), (2, 1)])
        self.assertTrue(all(r.context_kind == 'no_transfer' and r.session_key is None for r in rows))
        self.assertEqual(rows[0].diagnostic_metadata['reader_count'], 999)
        self.assertEqual(RawRFIDEvent.objects.count(), 0)

    def test_inactivity_and_later_epc_new_identity(self):
        self.capture.ingest([tag()])
        first = RFIDObservation.objects.get().event_uuid
        self.tick += 1.1
        self.capture.ingest([tag()])
        self.assertEqual(RFIDObservation.objects.get(event_uuid=first).finalization_reason, 'inactivity')
        self.assertEqual(RFIDObservation.objects.filter(status='open').count(), 1)
        self.assertEqual(ObservationOutbox.objects.count(), 1)

    def test_continuous_tag_max_duration(self):
        for second in range(24):
            self.tick = float(second) / 2
            self.capture.ingest([tag()])
        self.assertEqual(RFIDObservation.objects.count(), 2)
        self.assertEqual(RFIDObservation.objects.get(status='finalized').finalization_reason, 'max_duration')
        self.assertEqual(sum(RFIDObservation.objects.values_list('raw_read_count', flat=True)), 24)

    def test_capacity_finalizes_without_eviction(self):
        self.capture.config = ObservationConfig(active_key_limit=2)
        self.capture.ingest([tag('AA01'), tag('AA02'), tag('AA03'), tag('AA04')])
        self.assertEqual(RFIDObservation.objects.count(), 4)
        self.assertEqual(ObservationOutbox.objects.count(), 2)
        self.assertEqual(RFIDObservation.objects.filter(status='open').count(), 2)

    def test_restart_recovers_open_uuid_and_pending(self):
        self.capture.ingest([tag()])
        identity = RFIDObservation.objects.get().event_uuid
        restarted = ObservationCapture(device=self.device)
        restarted.recover()
        row = RFIDObservation.objects.get()
        self.assertEqual(row.event_uuid, identity)
        self.assertEqual(row.finalization_reason, 'restart')
        self.assertEqual(ObservationOutbox.objects.get().observation_id, row.pk)
        restarted.ingest([tag()])
        self.assertEqual(RFIDObservation.objects.count(), 2)

    def test_finalization_outbox_atomic_and_immutable(self):
        self.capture.ingest([tag()])
        with patch('bridge_core.observation_capture.ObservationOutbox.objects.bulk_create', side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError):
                self.capture.boundary('stop')
        self.assertEqual(RFIDObservation.objects.get().status, 'open')
        self.assertEqual(ObservationOutbox.objects.count(), 0)
        self.capture.boundary('stop')
        row = RFIDObservation.objects.get()
        row.epc = 'CCCC'
        with self.assertRaisesRegex(ValueError, 'immutable'):
            row.save()

    def test_boundary_context_snapshot(self):
        self.capture.ingest([tag()])
        self.capture.boundary('start', context_kind='receipt', session_key='one', operation='receipt')
        self.capture.ingest([tag()])
        self.capture.boundary('connection_failure', reconnect=True, context_kind='unresolved',
            session_key=None, operation=None, related_session_key='one')
        self.capture.ingest([tag()])
        self.capture.boundary('abort', context_kind='no_transfer', related_session_key=None)
        self.capture.ingest([tag()])
        rows = list(RFIDObservation.objects.order_by('id'))
        self.assertEqual([r.context_kind for r in rows], ['no_transfer', 'receipt', 'unresolved', 'no_transfer'])
        self.assertEqual(len({r.context_generation_uuid for r in rows}), 4)
        self.assertNotEqual(rows[1].connection_epoch_uuid, rows[2].connection_epoch_uuid)
        self.assertEqual(rows[1].frozen_payload['session_key'], 'one')

    def test_synthetic_100_1000_and_true_http_batches(self):
        for total in (100, 1000):
            with self.subTest(total=total):
                RFIDObservation.objects.filter(status='open').count()
                before = RFIDObservation.objects.count()
                with CaptureQueriesContext(connection) as queries:
                    self.capture.ingest([tag(f'{i:08X}') for i in range(total)])
                self.assertEqual(RFIDObservation.objects.count()-before, total)
                self.assertLess(len(queries), 50)  # bulk writes, not an ORM transaction per frame
                self.capture.boundary('stop')
                ObservationOutbox.objects.update(created_at=timezone.now()-timedelta(seconds=2))
                pending = ObservationOutbox.objects.filter(state='pending').count()
                self.api.observations.reset_mock()
                while self.worker.run_once():
                    pass
                self.assertEqual(self.api.observations.call_count, total//100)
                self.assertEqual(pending, total)
                self.assertEqual(ObservationOutbox.objects.filter(state='pending').count(), 0)
                print(f'SYNTHETIC unique={total} queries={len(queries)} observations={total} queue={pending} http_batches={self.api.observations.call_count}')

    def test_repeated_single_epc_grouped_before_write(self):
        with CaptureQueriesContext(connection) as queries:
            self.capture.ingest([tag()] * 1000)
        self.assertEqual(RFIDObservation.objects.get().raw_read_count, 1000)
        self.assertLess(len(queries), 10)

    def test_payload_size_split(self):
        self.finalize([tag(f'{i:08X}') for i in range(4)])
        one = RFIDObservation.objects.first().frozen_payload
        ceiling = encoded_size('gateway', self.device.code, [one, one]) + 20
        self.worker.config = ObservationConfig(max_payload_bytes=ceiling, batch_flush_seconds=0.01)
        self.worker.run_once()
        self.assertGreater(self.api.observations.call_count, 1)
        for call in self.api.observations.call_args_list:
            self.assertLessEqual(encoded_size('gateway', self.device.code, call.kwargs['observations']), ceiling)

    def test_partial_ambiguous_conflict_rejected_and_duplicate(self):
        self.finalize([tag(f'{i:08X}') for i in range(6)])
        rows = list(RFIDObservation.objects.order_by('pk'))
        self.api.observations.side_effect = None
        results = [{'event_uuid': str(rows[i].event_uuid), 'status': status} for i, status in enumerate(
            ['accepted', 'duplicate', 'conflict', 'rejected'])]
        results += [{'event_uuid': str(rows[4].event_uuid), 'status': 'accepted'}] * 2
        self.api.observations.return_value = {'ok': True, 'results': results}
        self.worker.run_once()
        self.assertEqual(ObservationOutbox.objects.filter(state='delivered').count(), 2)
        self.assertEqual(ObservationOutbox.objects.filter(state='quarantined').count(), 2)
        self.assertEqual(ObservationOutbox.objects.filter(state='pending').count(), 2)

    def test_lost_response_reuses_exact_uuid_payload(self):
        self.finalize()
        self.api.observations.side_effect = OdooRFIDApiTransportError('lost')
        self.worker.run_once()
        original = self.api.observations.call_args.kwargs['observations']
        self.due()
        self.api.observations.side_effect = lambda **kw: {'ok': True, 'results': [
            {'event_uuid': item['event_uuid'], 'status': 'duplicate'} for item in kw['observations']]}
        self.worker.run_once()
        self.assertEqual(self.api.observations.call_args.kwargs['observations'], original)
        self.assertEqual(ObservationOutbox.objects.get().state, 'delivered')
        self.assertEqual(ObservationDeliveryAttempt.objects.count(), 2)

    def test_429_retry_after_and_5xx(self):
        self.finalize()
        self.api.observations.side_effect = OdooRFIDApiTransportError('rate', status_code=429, retry_after='120')
        now = timezone.now()
        self.worker.run_once()
        self.assertGreaterEqual((ObservationOutbox.objects.get().next_attempt_at-now).total_seconds(), 120)
        self.due()
        self.worker.paused_until = None
        self.api.observations.side_effect = OdooRFIDApiTransportError('server', status_code=503)
        self.worker.run_once()
        self.assertEqual(ObservationOutbox.objects.get().state, 'pending')

    def test_413_reduces_batch(self):
        self.finalize([tag(f'{i:08X}') for i in range(4)])
        def response(**kw):
            if len(kw['observations']) > 1:
                raise OdooRFIDApiTransportError('large', status_code=413)
            return {'ok': True, 'results': [{'event_uuid': kw['observations'][0]['event_uuid'], 'status': 'accepted'}]}
        self.api.observations.side_effect = response
        self.worker.run_once()
        self.assertEqual(ObservationOutbox.objects.filter(state='delivered').count(), 4)
        self.assertEqual(self.api.observations.call_count, 7)

    def test_auth_pause_does_not_block_capture(self):
        for status in (401, 403):
            self.finalize()
            self.api.observations.side_effect = OdooRFIDApiTransportError('auth', status_code=status)
            self.worker.paused_until = None
            self.worker.run_once()
            calls = self.api.observations.call_count
            self.worker.run_once()
            self.assertEqual(calls, self.api.observations.call_count)
            self.capture.ingest([tag('CCDD')])
            self.assertTrue(RFIDObservation.objects.filter(epc='CCDD').exists())

    def test_expired_lease_recovery_and_fencing(self):
        self.finalize()
        old = self.worker.claim(timezone.now())[0]
        ObservationOutbox.objects.update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        fresh = self.worker.claim(timezone.now())[0]
        self.assertNotEqual(old.lease_token, fresh.lease_token)
        self.worker.finish(old, 'delivered')
        self.assertEqual(ObservationOutbox.objects.get().state, 'leased')
        self.worker.finish(fresh, 'delivered')
        self.assertEqual(ObservationOutbox.objects.get().state, 'delivered')
        self.assertTrue(ObservationDeliveryAttempt.objects.filter(outcome='lease_expired').exists())

    def test_malformed_response_never_acknowledges(self):
        self.finalize()
        self.api.observations.side_effect = None
        self.api.observations.return_value = {'ok': True, 'results': 'invalid'}
        self.worker.run_once()
        self.assertEqual(ObservationOutbox.objects.get().state, 'pending')

    def test_local_fingerprint_mismatch_is_quarantined(self):
        self.finalize()
        RFIDObservation.objects.update(payload_hash='bad')
        self.worker.run_once()
        self.assertEqual(ObservationOutbox.objects.get().state, 'quarantined')
        self.api.observations.assert_not_called()

    def test_queue_metrics(self):
        self.finalize()
        self.assertEqual(observation_metrics(self.device)['pending_count'], 1)
        self.assertIsNotNone(observation_metrics()['oldest_pending_at'])

    def test_invalid_frame_not_observation(self):
        self.capture.ingest([RFIDFrame(2, 1, COMMAND_ACTIVE_TAG, 0, b'bad', b'')])
        self.assertEqual(RFIDObservation.objects.count(), 0)

    def test_delayed_delivery_preserves_original_context(self):
        self.capture.boundary('start', context_kind='receipt', session_key='old', operation='receipt')
        self.finalize()
        self.capture.boundary('start', context_kind='delivery', session_key='new', operation='dispatch')
        self.worker.run_once()
        payload = self.api.observations.call_args.kwargs['observations'][0]
        self.assertEqual((payload['context_kind'], payload['session_key']), ('receipt', 'old'))

    def test_underfilled_batch_flush_wait_and_single_oversize(self):
        self.finalize()
        ObservationOutbox.objects.update(created_at=timezone.now())
        self.assertEqual(self.worker.run_once(), 0)
        ObservationOutbox.objects.update(created_at=timezone.now()-timedelta(seconds=2))
        self.worker.config = ObservationConfig(max_payload_bytes=20, batch_flush_seconds=0.01)
        self.worker.run_once()
        self.assertEqual(ObservationOutbox.objects.get().state, 'quarantined')
        self.api.observations.assert_not_called()

    def test_duplicate_changed_hash_is_conflict_and_malformed_item_isolated(self):
        self.finalize([tag('AA01'), tag('AA02'), tag('AA03')])
        rows = list(RFIDObservation.objects.order_by('pk'))
        self.api.observations.side_effect = None
        self.api.observations.return_value = {'ok': True, 'results': [
            {'event_uuid': str(rows[0].event_uuid), 'status': 'duplicate', 'payload_hash': 'different'},
            {'event_uuid': str(rows[1].event_uuid), 'status': 'rejected', 'reason_code': 'server_busy'},
            {'event_uuid': str(rows[2].event_uuid), 'status': 'accepted', 'outcome': 'rejected'}, None]}
        self.worker.run_once()
        self.assertEqual(ObservationOutbox.objects.filter(state='quarantined').count(), 1)
        self.assertEqual(ObservationOutbox.objects.filter(state='pending').count(), 2)



class SyntheticPhysical:
    def __init__(self):
        self.sink = None
        self.batches = []
        self.starts = 0
        self.stops = 0
        self.has_partial_frame = False

    def set_observation_sink(self, sink, *, connection_sink=None):
        self.sink = sink

    def poll_tag_frames(self, **kwargs):
        batch = self.batches.pop(0) if self.batches else ()
        if isinstance(batch, Exception):
            raise batch
        if self.sink:
            self.sink(batch)
        return batch

    def close(self):
        pass

    def verify_idle(self):
        pass

    def start(self, **kwargs):
        self.starts += 1

    def stop(self, **kwargs):
        self.stops += 1
        if self.sink:
            self.sink([tag('FF01')])

    def take_pending_tag_frames(self):
        return ()


class PipelineIntegrationTests(TestCase):
    def setUp(self):
        ObservationTests.setUp(self)
        self.physical = SyntheticPhysical()
        self.reader = ContinuousReaderSupervisor(physical=self.physical,
            device=self.device, tag_ingestor=ingest_final_active_tag_frames,
            observation_capture=self.capture)
        self.reader.step()
        self.runtime = FinalRuntimeOrchestrator(api_client=Mock(), reader_executor=self.reader)
        self.runtime.api_client.ack.return_value = {'ok': True}
        self.runtime.mark_reader_verified_idle()

    def command(self, name, key='one', operation='receipt'):
        return self.runtime.process_command(OdooRFIDCommand.from_payload(dict(
            session_key=key, reader_code=self.device.code, picking='WH/IN/001',
            operation=operation, command=name, revision={'start': 1, 'stop': 2, 'abort': 3}[name])))

    def emit(self, frames):
        self.physical.batches.append(frames)
        self.reader.step()

    def test_receipt_delivery_and_all_business_boundaries(self):
        self.emit([tag()])
        self.physical.batches.extend([[tag()], ()])  # admission reports are ambient
        self.assertTrue(self.command('start').success)
        self.emit([tag(), tag()])
        self.assertTrue(self.command('stop').success)
        self.emit([tag()])
        self.assertTrue(self.command('start', key='two', operation='dispatch').success)
        self.emit([tag()])
        self.assertTrue(self.command('abort', key='two').success)
        rows = list(RFIDObservation.objects.order_by('pk'))
        self.assertEqual([r.context_kind for r in rows], ['no_transfer', 'receipt', 'no_transfer', 'delivery'])
        self.assertEqual([r.raw_read_count for r in rows], [2, 2, 1, 1])
        self.assertEqual([r.finalization_reason for r in rows], ['start', 'stop', 'start', 'abort'])
        self.assertEqual(RawRFIDEvent.objects.count(), 2)
        self.assertEqual(self.physical.starts, 1)
        self.assertEqual(self.physical.stops, 0)

    def test_reconnect_poisoned_and_shutdown_control_frames(self):
        self.command('start')
        self.emit([tag()])
        old_epoch = RFIDObservation.objects.get().connection_epoch_uuid
        self.emit(OSError('reader disconnected'))
        self.emit([tag('CCDD')])
        self.assertEqual(RFIDObservation.objects.get(epc='CCDD').context_kind, 'unresolved')
        self.assertNotEqual(RFIDObservation.objects.get(epc='CCDD').connection_epoch_uuid, old_epoch)
        self.assertEqual(RawRFIDEvent.objects.count(), 1)
        self.assertTrue(self.command('abort').success)
        self.reader.shutdown()
        self.assertFalse(RFIDObservation.objects.filter(status='open').exists())
        self.assertEqual(RFIDObservation.objects.get(epc='FF01').finalization_reason, 'shutdown')

    def test_observation_and_transactional_failure_roll_back_together(self):
        self.command('start')
        self.reader.tag_ingestor = Mock(side_effect=RuntimeError('transactional failure'))
        self.emit([tag()])
        self.assertEqual(RawRFIDEvent.objects.count(), 0)
        # The business path fails closed; capture facts are recovered explicitly.
        self.assertEqual(RFIDObservation.objects.get().raw_read_count, 1)
        self.assertEqual(RFIDObservation.objects.get().finalization_reason, 'connection_failure')

    def test_control_response_batches_preserve_tags_on_both_sides_of_ack(self):
        session = FakeSession([
            [tag('AA01'), control_frame(command=COMMAND_STOP), tag('AA02')],
            [tag('AA03'), control_frame(command=COMMAND_START_INVENTORY), tag('AA04')],
            [tag('AA05'), control_frame(command=COMMAND_STOP), tag('AA06')],
        ])
        executor = PersistentActiveReaderExecutor(device=self.device,
            transport_factory=RecordingTransportFactory(session))
        executor.set_observation_sink(self.capture.ingest,
            connection_sink=lambda: self.capture.boundary('reader_connection', reconnect=True))
        executor.verify_idle()
        executor.start(session_key='hardware', reader_code=self.device.code)
        executor.stop(session_key='hardware', reader_code=self.device.code)
        self.capture.boundary('shutdown')
        self.assertEqual(list(RFIDObservation.objects.order_by('epc').values_list('epc', flat=True)),
                         ['AA01', 'AA02', 'AA03', 'AA04', 'AA05', 'AA06'])
        self.assertEqual(ObservationOutbox.objects.count(), 6)
        self.assertNotEqual(RFIDObservation.objects.get(epc='AA01').connection_epoch_uuid,
                            RFIDObservation.objects.get(epc='AA03').connection_epoch_uuid)
        self.assertTrue(session.closed)

    def test_real_transactional_barrier_ignores_observation_backlog(self):
        from bridge_core.final_worker_cycle import FinalWorkerCycle
        worker = FinalWorkerCycle(orchestrator=self.runtime, reader_executor=self.reader,
            device=self.device, capture_service=self.reader)
        self.runtime.before_success_ack = worker.require_stop_delivery_complete
        self.assertTrue(self.command('start').success)
        self.emit([tag(), tag()])
        self.assertFalse(self.command('stop').success)
        self.assertFalse(self.reader.is_active)
        self.assertEqual(ObservationOutbox.objects.get().state, 'pending')
        RawRFIDEvent.objects.update(queue_state='sent')
        self.assertTrue(self.command('stop').success)
        self.assertEqual(RawRFIDEvent.objects.count(), 1)
        self.assertEqual(self.physical.stops, 0)

    def test_storage_failure_retains_receive_until_recovery(self):
        self.command('start')
        with patch.object(self.capture, 'ingest', side_effect=RuntimeError('storage unavailable')):
            self.emit([tag()])
        self.assertEqual(RFIDObservation.objects.count(), 0)
        self.assertTrue(self.reader._retry_observations)
        self.reader.step()
        self.assertFalse(self.reader._retry_observations)
        self.assertEqual(RFIDObservation.objects.get().raw_read_count, 1)
        self.assertEqual(RFIDObservation.objects.get().context_kind, 'receipt')
        self.assertEqual(RawRFIDEvent.objects.count(), 0)
        self.assertIsNotNone(self.reader.window_error)


    def test_abort_before_storage_recovery_does_not_reclassify_failed_batch(self):
        self.command('start')
        with patch.object(self.capture, 'ingest', side_effect=RuntimeError('storage unavailable')):
            self.emit([tag()])
        self.assertTrue(self.command('abort').success)
        self.reader.step()
        row = RFIDObservation.objects.get()
        self.assertEqual((row.context_kind, row.session_key), ('receipt', 'one'))
        self.assertEqual(row.status, 'finalized')
        self.emit([tag('DD01')])
        self.assertEqual(RFIDObservation.objects.get(epc='DD01').context_kind, 'no_transfer')


    def test_shutdown_storage_failure_still_sends_stop_and_consumes_ack(self):
        session = FakeSession([[control_frame(command=COMMAND_START_INVENTORY)],
            [tag('AA01'), control_frame(command=COMMAND_STOP), tag('AA02')]])
        executor = PersistentActiveReaderExecutor(device=self.device,
            transport_factory=RecordingTransportFactory(session))
        reader = ContinuousReaderSupervisor(physical=executor, device=self.device,
            tag_ingestor=ingest_final_active_tag_frames, observation_capture=self.capture)
        executor.start(session_key=reader.HARDWARE_KEY, reader_code=self.device.code)
        from bridge_core.final_reader_supervisor import HardwareState
        from bridge_core.reader_protocol import parse_frame
        reader.state = HardwareState.ACTIVE
        with patch.object(self.capture, 'ingest', side_effect=RuntimeError('disk unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'disk unavailable'):
                reader.shutdown()
        self.assertEqual([parse_frame(data).command for data in session.sent],
                         [COMMAND_START_INVENTORY, COMMAND_STOP])
        self.assertEqual(session.batches, [])
        self.assertTrue(session.closed)
        self.assertTrue(reader._retry_observations)


    def test_verify_idle_connection_persistence_failure_still_checks_stop(self):
        session = FakeSession([[control_frame(command=COMMAND_STOP)]])
        executor = PersistentActiveReaderExecutor(device=self.device,
            transport_factory=RecordingTransportFactory(session))
        executor.set_observation_sink(self.capture.ingest,
            connection_sink=Mock(side_effect=RuntimeError('storage boundary failed')))
        with self.assertRaisesRegex(RuntimeError, 'storage boundary failed'):
            executor.verify_idle()
        self.assertEqual(len(session.sent), 1)
        self.assertEqual(session.batches, [])
        self.assertTrue(session.closed)



class ObservationConfigurationTests(TestCase):
    def test_defaults_and_overrides(self):
        self.assertEqual(ObservationConfig.load({}).batch_size, 100)
        self.assertEqual(ObservationConfig.load({'OBSERVATION_ACTIVE_KEY_LIMIT': '2000'}).active_key_limit, 2000)
        for value in ('nan', 'inf', '0', '-1'):
            with self.assertRaises(ValueError):
                ObservationConfig.load({'OBSERVATION_INACTIVITY_SECONDS': value})


class ObservationClientTests(TestCase):
    def test_actual_jsonrpc_batch_encoding_without_network(self):
        import json
        from bridge_core.odoo_api_v1 import OdooRFIDApiClient
        from bridge_core.test_odoo_api_v1 import RecordingOpenerFactory
        factory = RecordingOpenerFactory({'result': {'ok': True, 'results': []}})
        api = OdooRFIDApiClient(base_url='https://offline.invalid', bearer_token='offline-placeholder',
            gateway_code='gateway', opener_factory=factory)
        reports = [{'event_uuid': str(uuid.uuid4()), 'antenna': None}] * 100
        api.observations(reader_code='reader', observations=reports)
        self.assertEqual(len(factory.opener.requests), 1)
        request = factory.opener.requests[0]['request']
        self.assertTrue(request.full_url.endswith('/api/rfid/v1/observations'))
        self.assertEqual(len(request.data), encoded_size('gateway', 'reader', reports))
        params = json.loads(request.data)['params']
        self.assertEqual(params['schema_version'], 1)
        self.assertEqual(len(params['observations']), 100)

    def test_http_retry_after_is_preserved_without_body_or_credentials(self):
        from urllib.error import HTTPError
        from bridge_core.odoo_api_v1 import OdooRFIDApiClient
        opener = Mock()
        opener.open.side_effect = HTTPError('https://offline.invalid', 429, 'rate', {'Retry-After': '30'}, None)
        api = OdooRFIDApiClient(base_url='https://offline.invalid', bearer_token='offline-placeholder',
            gateway_code='gateway', opener_factory=Mock(return_value=opener))
        with self.assertRaises(OdooRFIDApiTransportError) as error:
            api.observations(reader_code='reader', observations=[])
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.retry_after, '30')
        self.assertNotIn('offline-placeholder', str(error.exception))

    def test_delivery_command_gate_prevents_network(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with patch('bridge_core.management.commands.run_rfid_observation_delivery_worker.OdooRFIDApiClient') as client:
            with self.assertRaises(CommandError):
                call_command('run_rfid_observation_delivery_worker', once=True)
            client.assert_not_called()
