"""Physical inventory supervision, independent of Odoo transaction windows.

Ambient frames are durably observed when an observation capture is configured.
Attachment requires an empty receive (a quiet
boundary); a busy reader fails attachment rather than inheriting buffered tags.
An interrupted window is never resumed automatically, even after reconnect.
"""
import threading
import time
import math
import logging
import uuid
from contextlib import nullcontext

from enum import Enum

from django.db import close_old_connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class HardwareState(str, Enum):
    OFFLINE = 'offline'
    ACTIVE = 'active'
    RECONNECTING = 'reconnecting'


class ReaderSupervisorError(RuntimeError):
    pass


class ContinuousReaderSupervisor:
    HARDWARE_KEY = '__continuous_inventory__'

    ADMISSION_SECONDS = 0.5
    ADMISSION_MAX_BATCHES = 8
    RECEIVE_SECONDS = 0.05

    def __init__(self, *, physical, device, tag_ingestor,
                 admission_seconds=ADMISSION_SECONDS,
                 admission_max_batches=ADMISSION_MAX_BATCHES,
                 receive_seconds=RECEIVE_SECONDS,
                 monotonic=time.monotonic, observation_capture=None):
        if (not math.isfinite(admission_seconds) or admission_seconds <= 0
                or not math.isfinite(receive_seconds) or receive_seconds <= 0
                or not isinstance(admission_max_batches, int)
                or admission_max_batches < 1):
            raise ValueError('Admission/receive limits must be finite and positive.')
        self.admission_seconds = admission_seconds
        self.admission_max_batches = admission_max_batches
        self.receive_seconds = receive_seconds
        self._monotonic = monotonic
        self._capture_transaction = False
        self._received_observations = []
        self._retry_observations = []
        self.observations = observation_capture
        self._operation = None
        self._revision = None
        if self.observations is not None:
            physical.set_observation_sink(self._observe,
                connection_sink=self._connection_opened)
        self.physical = physical
        self.device = device
        self.tag_ingestor = tag_ingestor
        self.state = HardwareState.OFFLINE
        self.error = None
        self.window_error = None
        self._session_key = None
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._thread = None

    @property
    def is_active(self):
        # Business executor interface: an attached window, not hardware health.
        return self._session_key is not None

    @property
    def active_session_key(self):
        return self._session_key

    def _connection_opened(self):
        try:
            self._boundary('reader_connection', reconnect=True)
        except Exception:
            # The physical epoch changed even if storage could not finalize the
            # previous one. Retained reports must never reuse that old key.
            self.observations.epoch = uuid.uuid4()
            self.observations.generation = uuid.uuid4()
            self.observations.context.update(
                context_kind='unresolved' if self.window_error else 'no_transfer',
                session_key=None, operation=None, command_revision=None,
                related_session_key=self._session_key if self.window_error else None,
                context_reason='connection_persistence_failure')
            raise

    def _observe(self, frames):
        batch = (tuple(frames), timezone.now(), self.observations.clock(), self.observations.snapshot())
        if self._capture_transaction:
            self._received_observations.append(batch)
        try:
            self.observations.ingest(batch[0], seen_at=batch[1], tick=batch[2], snapshot=batch[3])
        except Exception:
            if not self._capture_transaction:
                self._retry_observations.append(batch)
            raise

    def _recover_capture(self):
        # At most failed receive batches are retained while local storage is
        # unavailable. Do not consume further socket data until they are durable.
        recovered = len(self._retry_observations)
        while self._retry_observations:
            frames, seen_at, tick, snapshot = self._retry_observations[0]
            self.observations.ingest(frames, seen_at=seen_at, tick=tick, snapshot=snapshot)
            self._retry_observations.pop(0)
        if recovered:
            logger.info('observation_receive_recovery reader=%s batches=%s', self.device.code, recovered)

    def set_window_metadata(self, *, operation, revision):
        self._operation, self._revision = operation, revision

    def _boundary(self, reason, *, reconnect=False, context_kind=None,
                  session_key=None, operation=None, related_session_key=None):
        if self.observations is not None:
            self.observations.boundary(reason, reconnect=reconnect,
                context_kind=context_kind or ('unresolved' if self.window_error else 'no_transfer'),
                session_key=session_key, operation=operation,
                related_session_key=related_session_key or (self._session_key if self.window_error and context_kind != 'no_transfer' else None),
                command_revision=self._revision if session_key else None)

    def _connect(self):
        self._boundary('reconnect', reconnect=True)
        self.physical.close()
        self.physical.take_pending_tag_frames()
        self.physical.verify_idle()
        self.physical.start(session_key=self.HARDWARE_KEY,
                            reader_code=self.device.code)
        self.physical.take_pending_tag_frames()  # startup observations are ambient
        self.state = HardwareState.ACTIVE
        self.error = None

    def start_monitoring(self):
        with self._lock:
            if self._thread is not None:
                raise ReaderSupervisorError('Monitoring already started.')
            if self.observations is not None:
                self.observations.recover()
            self.step()
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name='continuous-rfid')
            self._thread.start()

    def step(self):
        """One serialized hardware iteration; exposed for deterministic tests."""
        with self._lock:
            try:
                if self.observations is not None and self._retry_observations:
                    self._recover_capture()
                if self.state != HardwareState.ACTIVE:
                    self._connect()
                self._capture_transaction = True
                self._received_observations = []
                with transaction.atomic() if self.observations is not None else nullcontext():
                    frames = tuple(self.physical.poll_tag_frames(
                        timeout_seconds=self.receive_seconds))
                    if frames and self._session_key and not self.window_error:
                        self.tag_ingestor(device=self.device,
                                          session_key=self._session_key, frames=frames)
                    if self.observations is not None and not frames:
                        self.observations.ingest(())  # expire idle aggregates on quiet reads
                self._capture_transaction = False
                self._received_observations = []
                if self.observations is not None:
                    self.observations.monitor()
                return True
            except Exception as exc:
                logger.error('reader_capture_failed reader=%s error_kind=%s', self.device.code, type(exc).__name__)
                self._capture_transaction = False
                self._retry_observations.extend(self._received_observations)
                self._received_observations = []
                self.error = str(exc)
                if self._session_key:
                    self.window_error = self.error
                self.state = HardwareState.RECONNECTING
                try:
                    if self.observations is not None:
                        self._recover_capture()
                    self._boundary('connection_failure')
                except Exception:
                    logger.error('observation_persistence_failure reader=%s storage_degraded=true', self.device.code)
                    logger.warning('observation_storage_pressure reader=%s capture_paused=true', self.device.code)
                self.physical.close()
                self.physical.take_pending_tag_frames()
                return False

    def _run(self):
        close_old_connections()
        delay = 0.25
        try:
            while not self._shutdown.is_set():
                healthy = self.step()
                delay = 0.25 if healthy else min(delay * 2, 10.0)
                self._shutdown.wait(0.01 if healthy else delay)
        finally:
            close_old_connections()

    def require_healthy(self):
        # Never wait behind a potentially slow physical reconnect for an ACK.
        if not self._lock.acquire(timeout=self.receive_seconds):
            raise ReaderSupervisorError('Physical reader busy; health cannot be verified.')
        try:
            if self.state != HardwareState.ACTIVE or self.window_error:
                raise ReaderSupervisorError(self.window_error or self.error or
                                            'Physical reader is offline.')

        finally:
            self._lock.release()

    def start(self, *, session_key, reader_code):
        deadline = self._monotonic() + self.admission_seconds
        if not self._lock.acquire(timeout=self.admission_seconds):
            raise ReaderSupervisorError('START admission deadline: reader busy.')
        try:
            self.require_healthy()
            if reader_code != self.device.code or not session_key:
                raise ReaderSupervisorError('Invalid window identity.')
            if self._session_key:
                raise ReaderSupervisorError('A business window is already attached.')
            # The deadline includes lock acquisition, every receive and decoder
            # checks. A short receive budget overrides even a 300s device timeout.
            for _ in range(self.admission_max_batches):
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                try:
                    frames = self.physical.poll_tag_frames(
                        timeout_seconds=min(self.receive_seconds, remaining))
                except Exception as exc:
                    self.error = str(exc)
                    self.state = HardwareState.RECONNECTING
                    raise ReaderSupervisorError(
                        f'START admission reader failure: {exc}') from exc
                if self.physical.has_partial_frame:
                    raise ReaderSupervisorError(
                        'START admission rejected: partial protocol frame.')
                if self._monotonic() >= deadline:
                    break
                if not frames:
                    self._boundary('start', context_kind='receipt' if self._operation == 'receipt' else
                                   'delivery' if self._operation == 'dispatch' else 'unresolved',
                                   session_key=session_key, operation=self._operation)
                    self._session_key = session_key
                    return
            raise ReaderSupervisorError(
                'START admission failed: no clean quiet boundary within deadline/batch limit.')
        finally:
            self._lock.release()

    def stop(self, *, session_key, reader_code):
        with self._lock:
            self.require_healthy()
            if reader_code != self.device.code or self._session_key != session_key:
                raise ReaderSupervisorError('Window identity mismatch.')
            self._boundary('stop', context_kind='no_transfer')
            self._session_key = None

    def abort_window(self, *, session_key, reader_code):
        with self._lock:
            if reader_code != self.device.code or self._session_key not in (None, session_key):
                raise ReaderSupervisorError('Window identity mismatch.')
            self._boundary('abort', context_kind='no_transfer')
            self._session_key = None
            self.window_error = None

    def take_pending_tag_frames(self):
        # All assigned observations are persisted under the same lock as detach.
        return ()

    def shutdown(self):
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise ReaderSupervisorError('Capture did not stop; socket still owned.')
        with self._lock:
            hardware_error = persistence_error = None
            try:
                if self.state != HardwareState.ACTIVE:
                    self.physical.close()
                    self.physical.verify_idle()  # STOP ACK still required
                else:
                    self.physical.stop(session_key=self.HARDWARE_KEY,
                                       reader_code=self.device.code)
            except Exception as exc:
                hardware_error = exc
            try:
                if self.observations is not None and self._retry_observations:
                    self._recover_capture()
                self._boundary('shutdown')
            except Exception as exc:
                persistence_error = exc
                logger.error('observation_shutdown_persistence_failure reader=%s', self.device.code)
            finally:
                self.physical.close()
                self.state = HardwareState.OFFLINE
            if hardware_error is not None:
                raise hardware_error
            if persistence_error is not None:
                raise persistence_error
