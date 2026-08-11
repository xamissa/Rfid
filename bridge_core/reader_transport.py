from dataclasses import dataclass
import socket
from typing import Callable

from bridge_core.reader_protocol import (
    FrameStreamDecoder,
    RFIDFrame,
)


class RFIDTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class TCPReaderConnection:
    host: str
    port: int
    connect_timeout_seconds: float
    read_timeout_seconds: float


SocketFactory = Callable[
    [tuple[str, int], float],
    socket.socket,
]


def validate_connection(
    connection: TCPReaderConnection,
) -> None:
    if not connection.host.strip():
        raise RFIDTransportError(
            "Reader host cannot be empty."
        )

    if not 1 <= connection.port <= 65535:
        raise RFIDTransportError(
            "Reader TCP port must be between 1 and 65535."
        )

    if not 0 < connection.connect_timeout_seconds <= 300:
        raise RFIDTransportError(
            "Reader connect timeout must be between 0 and 300 seconds."
        )

    if not 0 < connection.read_timeout_seconds <= 300:
        raise RFIDTransportError(
            "Reader read timeout must be between 0 and 300 seconds."
        )


class TCPReaderSession:
    def __init__(
        self,
        *,
        connection: TCPReaderConnection,
        socket_factory: SocketFactory,
        receive_size: int,
    ):
        self._connection = connection
        self._socket_factory = socket_factory
        self._receive_size = receive_size
        self._socket = None
        self._decoder = FrameStreamDecoder()

    def __enter__(self):
        if self._socket is not None:
            raise RFIDTransportError(
                "RFID TCP session is already open."
            )

        try:
            self._socket = self._socket_factory(
                (
                    self._connection.host,
                    self._connection.port,
                ),
                self._connection.connect_timeout_seconds,
            )
            self._socket.settimeout(
                self._connection.read_timeout_seconds
            )
        except (OSError, socket.timeout) as exc:
            self._socket = None
            raise RFIDTransportError(
                f"RFID TCP connection failed: {exc}"
            ) from exc

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        self.close()

    @property
    def is_open(self) -> bool:
        return self._socket is not None

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def send(self, outbound_frame: bytes) -> None:
        if self._socket is None:
            raise RFIDTransportError(
                "RFID TCP session is not open."
            )

        if not outbound_frame:
            raise RFIDTransportError(
                "Outbound frame cannot be empty."
            )

        try:
            self._socket.sendall(bytes(outbound_frame))
        except (OSError, socket.timeout) as exc:
            raise RFIDTransportError(
                f"RFID TCP send failed: {exc}"
            ) from exc

    def receive(
        self,
        *,
        expected_commands: tuple[int, ...],
        maximum_reads: int = 32,
        timeout_returns_empty: bool = False,
    ) -> tuple[RFIDFrame, ...]:
        if self._socket is None:
            raise RFIDTransportError(
                "RFID TCP session is not open."
            )

        if not expected_commands:
            raise RFIDTransportError(
                "At least one expected response command is required."
            )

        if maximum_reads < 1:
            raise RFIDTransportError(
                "Maximum reads must be at least one."
            )

        matched_frames = []

        try:
            for _ in range(maximum_reads):
                chunk = self._socket.recv(self._receive_size)

                if not chunk:
                    break

                for frame in self._decoder.feed(chunk):
                    if frame.command in expected_commands:
                        matched_frames.append(frame)

                if matched_frames:
                    return tuple(matched_frames)
        except socket.timeout as exc:
            if timeout_returns_empty:
                return ()

            raise RFIDTransportError(
                f"RFID TCP receive failed: {exc}"
            ) from exc
        except OSError as exc:
            raise RFIDTransportError(
                f"RFID TCP receive failed: {exc}"
            ) from exc

        if self._decoder.pending_bytes:
            raise RFIDTransportError(
                "Connection ended with an incomplete RFID frame."
            )

        raise RFIDTransportError(
            "No expected RFID response frame was received."
        )

    def exchange(
        self,
        *,
        outbound_frame: bytes,
        expected_commands: tuple[int, ...],
        maximum_reads: int = 32,
    ) -> tuple[RFIDFrame, ...]:
        self.send(outbound_frame)

        return self.receive(
            expected_commands=expected_commands,
            maximum_reads=maximum_reads,
        )


class TCPReaderTransport:
    def __init__(
        self,
        *,
        connection: TCPReaderConnection,
        socket_factory: SocketFactory = socket.create_connection,
        receive_size: int = 4096,
    ):
        validate_connection(connection)

        if receive_size < 9:
            raise RFIDTransportError(
                "Receive size must be at least nine bytes."
            )

        self._connection = connection
        self._socket_factory = socket_factory
        self._receive_size = receive_size

    def open_session(self) -> TCPReaderSession:
        return TCPReaderSession(
            connection=self._connection,
            socket_factory=self._socket_factory,
            receive_size=self._receive_size,
        )

    def exchange(
        self,
        *,
        outbound_frame: bytes,
        expected_commands: tuple[int, ...],
        maximum_reads: int = 32,
    ) -> tuple[RFIDFrame, ...]:
        if not outbound_frame:
            raise RFIDTransportError(
                "Outbound frame cannot be empty."
            )

        if not expected_commands:
            raise RFIDTransportError(
                "At least one expected response command is required."
            )

        if maximum_reads < 1:
            raise RFIDTransportError(
                "Maximum reads must be at least one."
            )

        with self.open_session() as session:
            return session.exchange(
                outbound_frame=outbound_frame,
                expected_commands=expected_commands,
                maximum_reads=maximum_reads,
            )
