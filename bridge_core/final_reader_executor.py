import struct

from bridge_core.models import ReaderDevice
from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    COMMAND_START_INVENTORY,
    COMMAND_STOP,
    RFIDFrame,
    build_frame,
)
from bridge_core.reader_transport import (
    TCPReaderConnection,
    TCPReaderTransport,
)


class FinalReaderExecutorError(RuntimeError):
    pass


class PersistentActiveReaderExecutor:
    """
    Persistent reader control for the final Odoo-owned RFID session.

    START opens the TCP session and leaves inventory running.
    STOP/ABORT explicitly stops inventory and closes the TCP session.

    This intentionally does not use ActiveInventoryReaderClient.run(),
    because that legacy POC client always stops after a fixed duration.
    """

    ACCEPTED_START_STATUSES = frozenset((0, 0x99))

    def __init__(
        self,
        *,
        device,
        transport_factory=TCPReaderTransport,
    ):
        self.device = device
        self._transport_factory = transport_factory

        self._transport = None
        self._session = None
        self._active_session_key = None
        self._pending_tag_frames = []

        self._validate_device()

    @property
    def is_active(self):
        return (
            self._session is not None
            and self._active_session_key is not None
        )

    @property
    def active_session_key(self):
        return self._active_session_key

    def _validate_device(self):
        if not self.device.enabled:
            raise FinalReaderExecutorError(
                "Final RFID reader must be enabled."
            )

        if (
            self.device.inventory_mode
            != ReaderDevice.InventoryMode.ACTIVE
        ):
            raise FinalReaderExecutorError(
                "Final RFID reader must use active inventory mode."
            )

        if not str(self.device.host or "").strip():
            raise FinalReaderExecutorError(
                "Final RFID reader host cannot be empty."
            )

    def _build_transport(self):
        connection = TCPReaderConnection(
            host=self.device.host.strip(),
            port=self.device.port,
            connect_timeout_seconds=(
                self.device.connect_timeout_seconds
            ),
            read_timeout_seconds=(
                self.device.read_timeout_seconds
            ),
        )

        return self._transport_factory(
            connection=connection
        )

    def _validate_frame_address(self, frame):
        if frame.address != self.device.device_address:
            raise FinalReaderExecutorError(
                "RFID response address does not match configured reader."
            )

    def _capture_tag_frame(self, frame):
        self._validate_frame_address(frame)

        if frame.command != COMMAND_ACTIVE_TAG:
            return

        if frame.status != 0:
            raise FinalReaderExecutorError(
                "RFID reader returned a failed active-tag frame."
            )

        self._pending_tag_frames.append(frame)

    def _wait_for_start_response(self):
        for _ in range(32):
            frames = self._session.receive(
                expected_commands=(
                    COMMAND_START_INVENTORY,
                    COMMAND_ACTIVE_TAG,
                ),
                maximum_reads=32,
                timeout_returns_empty=True,
            )

            if not frames:
                continue

            for frame in frames:
                self._validate_frame_address(frame)

                if frame.command == COMMAND_ACTIVE_TAG:
                    self._capture_tag_frame(frame)
                    continue

                if frame.command != COMMAND_START_INVENTORY:
                    continue

                if (
                    frame.status
                    not in self.ACCEPTED_START_STATUSES
                ):
                    raise FinalReaderExecutorError(
                        "RFID reader rejected active inventory START "
                        f"with status {frame.status}."
                    )

                return frame

        raise FinalReaderExecutorError(
            "No active inventory START response was received."
        )

    def _wait_for_stop_response(self):
        for _ in range(32):
            frames = self._session.receive(
                expected_commands=(
                    COMMAND_STOP,
                    COMMAND_ACTIVE_TAG,
                ),
                maximum_reads=32,
                timeout_returns_empty=True,
            )

            if not frames:
                continue

            for frame in frames:
                self._validate_frame_address(frame)

                if frame.command == COMMAND_ACTIVE_TAG:
                    self._capture_tag_frame(frame)
                    continue

                if frame.command != COMMAND_STOP:
                    continue

                if frame.status != 0:
                    raise FinalReaderExecutorError(
                        "RFID reader rejected STOP "
                        f"with status {frame.status}."
                    )

                return frame

        raise FinalReaderExecutorError(
            "No active inventory STOP response was received."
        )

    def verify_idle(self):
        """
        Establish a known-safe reader state after process startup.

        A fresh TCP session sends STOP and requires a successful STOP
        response. This never resumes a previous inventory session.
        """
        if self.is_active:
            raise FinalReaderExecutorError(
                "Cannot perform startup verification while reader is active."
            )

        transport = self._build_transport()
        session_context = transport.open_session()
        session = None

        try:
            session = session_context.__enter__()

            stop_request = build_frame(
                address=self.device.device_address,
                sequence=11,
                command=COMMAND_STOP,
            )

            session.send(stop_request)

            for _ in range(32):
                frames = session.receive(
                    expected_commands=(COMMAND_STOP,),
                    maximum_reads=32,
                    timeout_returns_empty=True,
                )

                if not frames:
                    continue

                for frame in frames:
                    self._validate_frame_address(frame)

                    if frame.command != COMMAND_STOP:
                        continue

                    if frame.status != 0:
                        raise FinalReaderExecutorError(
                            "RFID startup STOP verification failed "
                            f"with status {frame.status}."
                        )

                    return

            raise FinalReaderExecutorError(
                "No STOP response received during startup verification."
            )

        finally:
            if session is not None:
                session.close()

    def start(
        self,
        *,
        session_key,
        reader_code,
    ):
        session_key = str(session_key or "").strip()
        reader_code = str(reader_code or "").strip()

        if reader_code != self.device.code:
            raise FinalReaderExecutorError(
                "START reader identity mismatch."
            )

        if not session_key:
            raise FinalReaderExecutorError(
                "START requires a session key."
            )

        if self.is_active:
            if self._active_session_key == session_key:
                return

            raise FinalReaderExecutorError(
                "Reader already has a different active session."
            )

        self._transport = self._build_transport()
        session_context = self._transport.open_session()

        try:
            self._session = session_context.__enter__()

            start_request = build_frame(
                address=self.device.device_address,
                sequence=10,
                command=COMMAND_START_INVENTORY,
                payload=struct.pack(">I", 0x00000050),
            )

            self._session.send(start_request)
            self._wait_for_start_response()

            self._active_session_key = session_key

        except Exception:
            try:
                session_context.__exit__(
                    None,
                    None,
                    None,
                )
            finally:
                self._session = None
                self._transport = None
                self._active_session_key = None
            raise

    def stop(
        self,
        *,
        session_key,
        reader_code,
    ):
        session_key = str(session_key or "").strip()
        reader_code = str(reader_code or "").strip()

        if reader_code != self.device.code:
            raise FinalReaderExecutorError(
                "STOP reader identity mismatch."
            )

        if not self.is_active:
            raise FinalReaderExecutorError(
                "Reader has no active persistent session."
            )

        if session_key != self._active_session_key:
            raise FinalReaderExecutorError(
                "STOP session key does not match active reader session."
            )

        try:
            stop_request = build_frame(
                address=self.device.device_address,
                sequence=11,
                command=COMMAND_STOP,
            )

            self._session.send(stop_request)
            self._wait_for_stop_response()

        finally:
            self.close()

    def poll_tag_frames(self):
        if not self.is_active:
            return ()

        frames = self._session.receive(
            expected_commands=(COMMAND_ACTIVE_TAG,),
            maximum_reads=32,
            timeout_returns_empty=True,
        )

        for frame in frames:
            self._capture_tag_frame(frame)

        return self.take_pending_tag_frames()

    def take_pending_tag_frames(self):
        frames = tuple(self._pending_tag_frames)
        self._pending_tag_frames.clear()
        return frames

    def close(self):
        if self._session is not None:
            self._session.close()

        self._session = None
        self._transport = None
        self._active_session_key = None
