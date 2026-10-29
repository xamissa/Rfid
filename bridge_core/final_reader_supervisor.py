"""Physical inventory supervision, independent of Odoo transaction windows.

Ambient frames are discarded. Attachment requires an empty receive (a quiet
boundary); a busy reader fails attachment rather than inheriting buffered tags.
An interrupted window is never resumed automatically, even after reconnect.
"""
import threading
import time
import math
from enum import Enum

from django.db import close_old_connections


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
                 monotonic=time.monotonic):
        if (not math.isfinite(admission_seconds) or admission_seconds <= 0
                or not math.isfinite(receive_seconds) or receive_seconds <= 0
                or not isinstance(admission_max_batches, int)
                or admission_max_batches < 1):
            raise ValueError('Admission/receive limits must be finite and positive.')
        self.admission_seconds = admission_seconds
        self.admission_max_batches = admission_max_batches
        self.receive_seconds = receive_seconds
        self._monotonic = monotonic
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

    def _connect(self):
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
            self.step()
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name='continuous-rfid')
            self._thread.start()

    def step(self):
        """One serialized hardware iteration; exposed for deterministic tests."""
        with self._lock:
            try:
                if self.state != HardwareState.ACTIVE:
                    self._connect()
                frames = tuple(self.physical.poll_tag_frames(
                    timeout_seconds=self.receive_seconds))
                if frames and self._session_key and not self.window_error:
                    self.tag_ingestor(device=self.device,
                                      session_key=self._session_key, frames=frames)
                return True
            except Exception as exc:
                self.error = str(exc)
                if self._session_key:
                    self.window_error = self.error
                self.state = HardwareState.RECONNECTING
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
            self._session_key = None

    def abort_window(self, *, session_key, reader_code):
        with self._lock:
            if reader_code != self.device.code or self._session_key not in (None, session_key):
                raise ReaderSupervisorError('Window identity mismatch.')
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
            try:
                if self.state != HardwareState.ACTIVE:
                    self.physical.close()
                    self.physical.verify_idle()  # STOP ACK still required
                else:
                    self.physical.stop(session_key=self.HARDWARE_KEY,
                                       reader_code=self.device.code)
            finally:
                self.physical.close()
                self.state = HardwareState.OFFLINE
