from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from bridge_core.final_reader_supervisor import (
    ContinuousReaderSupervisor, HardwareState, ReaderSupervisorError,
)


class ContinuousReaderTests(TestCase):
    def setUp(self):
        self.physical = Mock()
        self.physical.has_partial_frame = False
        self.physical.poll_tag_frames.return_value = ()
        self.ingest = Mock()
        self.reader = ContinuousReaderSupervisor(
            physical=self.physical, device=SimpleNamespace(code='reader'),
            tag_ingestor=self.ingest)

    def attach(self, key='one'):
        self.reader.start(session_key=key, reader_code='reader')

    def test_startup_and_ambient(self):
        self.reader.step()
        self.physical.poll_tag_frames.return_value = ('ambient',)
        self.reader.step()
        self.reader.step()
        self.physical.start.assert_called_once()
        self.ingest.assert_not_called()
        self.assertEqual(self.reader.state, HardwareState.ACTIVE)

    def test_window_boundaries(self):
        self.reader.step()
        self.physical.poll_tag_frames.side_effect = [('stale',), ()]
        self.attach()
        self.physical.poll_tag_frames.side_effect = None
        self.physical.poll_tag_frames.return_value = ('assigned',)
        self.reader.step()
        self.reader.stop(session_key='one', reader_code='reader')
        self.reader.step()
        self.ingest.assert_called_once_with(device=self.reader.device,
                                            session_key='one', frames=('assigned',))
        self.physical.stop.assert_not_called()
        self.physical.poll_tag_frames.side_effect = [('old',), ()]
        self.attach('two')
        self.physical.start.assert_called_once()

    def test_abort_and_exclusivity(self):
        self.reader.step()
        self.attach()
        with self.assertRaises(ReaderSupervisorError):
            self.attach('two')
        self.reader.abort_window(session_key='one', reader_code='reader')
        self.physical.stop.assert_not_called()
        self.attach('two')

    def test_timeout_empty_does_not_reconnect(self):
        for _ in range(4):
            self.assertTrue(self.reader.step())
        self.physical.start.assert_called_once()

    def test_failure_reconnect_does_not_resume_window(self):
        self.reader.step()
        self.attach()
        self.physical.poll_tag_frames.side_effect = OSError('connection lost')
        self.assertFalse(self.reader.step())
        self.assertEqual(self.reader.state, HardwareState.RECONNECTING)
        self.physical.poll_tag_frames.side_effect = None
        self.physical.poll_tag_frames.return_value = ('after recovery',)
        self.assertTrue(self.reader.step())
        self.assertEqual(self.physical.start.call_count, 2)
        self.ingest.assert_not_called()
        with self.assertRaises(ReaderSupervisorError):
            self.reader.stop(session_key='one', reader_code='reader')
        self.reader.abort_window(session_key='one', reader_code='reader')

    def test_shutdown(self):
        self.reader.step()
        self.reader.shutdown()
        self.physical.stop.assert_called_once_with(
            session_key=self.reader.HARDWARE_KEY, reader_code='reader')
        self.assertEqual(self.reader.state, HardwareState.OFFLINE)

    def test_busy_boundary_rejects_start(self):
        self.reader.step()
        self.physical.poll_tag_frames.return_value = ('ambient',)
        with self.assertRaises(ReaderSupervisorError):
            self.attach()
        self.assertFalse(self.reader.is_active)

    def test_partial_ambient_frame_blocks_attachment(self):
        self.reader.step()
        self.physical.has_partial_frame = True
        with self.assertRaises(ReaderSupervisorError):
            self.attach()
        self.assertFalse(self.reader.is_active)

    def test_admission_deadline_limits_reads_and_never_attaches_after_expiry(self):
        from unittest.mock import Mock
        clock = Mock(side_effect=[10.0, 10.0, 10.2, 10.2, 10.5])
        reader = ContinuousReaderSupervisor(
            physical=self.physical, device=self.reader.device,
            tag_ingestor=self.ingest, monotonic=clock,
            admission_seconds=0.5, receive_seconds=0.25)
        reader.step()
        self.physical.poll_tag_frames.reset_mock()
        self.physical.poll_tag_frames.side_effect = [('ambient',), ()]
        with self.assertRaisesRegex(ReaderSupervisorError, 'deadline/batch limit'):
            reader.start(session_key='one', reader_code='reader')
        self.assertEqual(self.physical.poll_tag_frames.call_count, 2)
        self.assertFalse(reader.is_active)
        self.assertEqual(reader.state, HardwareState.ACTIVE)

    def test_batch_limit_is_independent_of_clock_progress(self):
        reader = ContinuousReaderSupervisor(
            physical=self.physical, device=self.reader.device,
            tag_ingestor=self.ingest, monotonic=lambda: 1,
            admission_max_batches=2)
        reader.step()
        self.physical.poll_tag_frames.reset_mock()
        self.physical.poll_tag_frames.return_value = ('ambient',)
        with self.assertRaisesRegex(ReaderSupervisorError, 'quiet boundary'):
            reader.start(session_key='one', reader_code='reader')
        self.assertEqual(self.physical.poll_tag_frames.call_count, 2)
        self.assertFalse(reader.is_active)

    def test_lock_contention_has_same_admission_budget(self):
        from unittest.mock import Mock
        lock = Mock()
        lock.acquire.return_value = False
        self.reader._lock = lock
        with self.assertRaisesRegex(ReaderSupervisorError, 'deadline: reader busy'):
            self.attach()
        lock.acquire.assert_called_once_with(timeout=self.reader.admission_seconds)
        self.physical.poll_tag_frames.assert_not_called()

    def test_partial_frame_rejects_immediately_without_repeated_timeouts(self):
        self.reader.step()
        self.physical.poll_tag_frames.reset_mock()
        self.physical.has_partial_frame = True
        with self.assertRaisesRegex(ReaderSupervisorError, 'partial protocol frame'):
            self.attach()
        self.physical.poll_tag_frames.assert_called_once()
        self.assertEqual(self.reader.state, HardwareState.ACTIVE)

    def test_socket_failure_during_admission_is_degraded_not_quiet(self):
        self.reader.step()
        self.physical.poll_tag_frames.side_effect = OSError('socket failed')
        with self.assertRaisesRegex(ReaderSupervisorError, 'admission reader failure'):
            self.attach()
        self.assertFalse(self.reader.is_active)
        self.assertEqual(self.reader.state, HardwareState.RECONNECTING)
