import threading
import time

from django.db import close_old_connections


class FinalCaptureServiceError(RuntimeError):
    pass


class FinalCaptureService:
    """
    Continuously drains active RFID tag frames independently of Odoo HTTP.

    The capture thread never contacts Odoo. Its only responsibilities are:
    - poll the already-active reader executor;
    - persist returned frames through the supplied tag ingestor;
    - retain any capture failure for the control thread to observe.

    START and STOP remain owned by the control/orchestrator thread.
    """

    def __init__(
        self,
        *,
        reader_executor,
        device,
        tag_ingestor,
        idle_sleep_seconds=0.01,
        join_timeout_seconds=10.0,
    ):
        idle_sleep_seconds = float(
            idle_sleep_seconds
        )
        join_timeout_seconds = float(
            join_timeout_seconds
        )

        if idle_sleep_seconds <= 0:
            raise FinalCaptureServiceError(
                "Capture idle sleep must be positive."
            )

        if join_timeout_seconds <= 0:
            raise FinalCaptureServiceError(
                "Capture join timeout must be positive."
            )

        self.reader_executor = reader_executor
        self.device = device
        self.tag_ingestor = tag_ingestor
        self.idle_sleep_seconds = (
            idle_sleep_seconds
        )
        self.join_timeout_seconds = (
            join_timeout_seconds
        )

        self._thread = None
        self._stop_event = threading.Event()
        self._session_key = None
        self._error = None
        self._lock = threading.Lock()

    @property
    def is_running(self):
        thread = self._thread

        return (
            thread is not None
            and thread.is_alive()
        )

    @property
    def session_key(self):
        return self._session_key

    @property
    def error(self):
        return self._error

    def start(
        self,
        *,
        session_key,
        reader_code,
    ):
        session_key = str(
            session_key or ""
        ).strip()

        reader_code = str(
            reader_code or ""
        ).strip()

        if reader_code != self.device.code:
            raise FinalCaptureServiceError(
                "Capture reader identity mismatch."
            )

        if not session_key:
            raise FinalCaptureServiceError(
                "Capture requires a session key."
            )

        if self.is_running:
            if self._session_key == session_key:
                return

            raise FinalCaptureServiceError(
                "Capture service already owns a different session."
            )

        if not self.reader_executor.is_active:
            raise FinalCaptureServiceError(
                "Cannot start capture while reader is inactive."
            )

        if (
            self.reader_executor.active_session_key
            != session_key
        ):
            raise FinalCaptureServiceError(
                "Capture session does not match active reader session."
            )

        self._stop_event.clear()
        self._error = None
        self._session_key = session_key

        self._thread = threading.Thread(
            target=self._run,
            name=(
                "rfid-capture-"
                f"{self.device.code}"
            ),
            daemon=True,
        )

        self._thread.start()

    def _run(self):
        close_old_connections()

        try:
            while not self._stop_event.is_set():
                frames = tuple(
                    self.reader_executor.poll_tag_frames()
                )

                if frames:
                    self.tag_ingestor(
                        device=self.device,
                        session_key=self._session_key,
                        frames=frames,
                    )
                    continue

                time.sleep(
                    self.idle_sleep_seconds
                )

        except Exception as exc:
            with self._lock:
                self._error = exc

        finally:
            self._stop_event.set()
            close_old_connections()

    def stop(self):
        thread = self._thread

        if thread is None:
            self._session_key = None
            return

        self._stop_event.set()

        thread.join(
            timeout=self.join_timeout_seconds
        )

        if thread.is_alive():
            raise FinalCaptureServiceError(
                "RFID capture thread did not stop within timeout."
            )

        self._thread = None
        self._session_key = None

    def require_healthy(self):
        error = self._error

        if error is not None:
            raise FinalCaptureServiceError(
                f"RFID capture failed: {error}"
            ) from error

        return True
