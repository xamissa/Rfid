from dataclasses import dataclass
import struct
import time
from typing import Callable

from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    COMMAND_CACHE_TAG,
    COMMAND_HANDSHAKE,
    COMMAND_INVENTORY_STATISTICS,
    COMMAND_START_INVENTORY,
    COMMAND_STOP,
    RFIDFrame,
    RFIDTagData,
    build_frame,
    parse_inventory_statistics,
    parse_tag_data,
)


class RFIDReaderClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class CachedInventoryResult:
    statistics_frame: RFIDFrame
    duration_ms: int
    read_count: int
    expected_tag_count: int
    tags: tuple[RFIDTagData, ...]


def _sequence(value: int) -> int:
    return value % 256


def _require_success(
    frame: RFIDFrame,
    *,
    expected_address: int,
    expected_command: int,
) -> None:
    if frame.address != expected_address:
        raise RFIDReaderClientError(
            "RFID response address does not match the configured reader."
        )

    if frame.command != expected_command:
        raise RFIDReaderClientError(
            "RFID response command does not match the request."
        )

    if frame.status != 0:
        raise RFIDReaderClientError(
            f"RFID reader returned failure status {frame.status}."
        )


class CachedInventoryReaderClient:
    def __init__(
        self,
        *,
        address: int,
        scan_seconds: float = 3.0,
        sleep_function: Callable[[float], None] = time.sleep,
    ):
        if not 0 <= address <= 255:
            raise RFIDReaderClientError(
                "Reader address must be between 0 and 255."
            )

        if not 0 <= scan_seconds <= 300:
            raise RFIDReaderClientError(
                "Scan duration must be between 0 and 300 seconds."
            )

        self._address = address
        self._scan_seconds = scan_seconds
        self._sleep = sleep_function

    def run(self, *, session) -> CachedInventoryResult:
        handshake_frames = session.exchange(
            outbound_frame=build_frame(
                address=self._address,
                sequence=1,
                command=COMMAND_HANDSHAKE,
                payload=struct.pack(">I", 1),
            ),
            expected_commands=(COMMAND_HANDSHAKE,),
        )

        if len(handshake_frames) != 1:
            raise RFIDReaderClientError(
                "Expected exactly one handshake response."
            )

        _require_success(
            handshake_frames[0],
            expected_address=self._address,
            expected_command=COMMAND_HANDSHAKE,
        )

        session.send(
            build_frame(
                address=self._address,
                sequence=10,
                command=COMMAND_START_INVENTORY,
                payload=struct.pack(">I", 1),
            )
        )

        self._sleep(self._scan_seconds)

        session.send(
            build_frame(
                address=self._address,
                sequence=11,
                command=COMMAND_STOP,
            )
        )

        statistics_frames = session.receive(
            expected_commands=(
                COMMAND_INVENTORY_STATISTICS,
            ),
        )

        if len(statistics_frames) != 1:
            raise RFIDReaderClientError(
                "Expected exactly one inventory statistics response."
            )

        statistics_frame = statistics_frames[0]

        _require_success(
            statistics_frame,
            expected_address=self._address,
            expected_command=COMMAND_INVENTORY_STATISTICS,
        )

        statistics = parse_inventory_statistics(
            statistics_frame.payload
        )
        tags = []

        for index in range(statistics.tag_count):
            cache_frames = session.exchange(
                outbound_frame=build_frame(
                    address=self._address,
                    sequence=_sequence(20 + index),
                    command=COMMAND_CACHE_TAG,
                    payload=struct.pack(">i", index),
                ),
                expected_commands=(COMMAND_CACHE_TAG,),
            )

            if len(cache_frames) != 1:
                raise RFIDReaderClientError(
                    "Expected exactly one cache-row response."
                )

            cache_frame = cache_frames[0]

            _require_success(
                cache_frame,
                expected_address=self._address,
                expected_command=COMMAND_CACHE_TAG,
            )

            tag = parse_tag_data(cache_frame.payload)

            if not tag.epc:
                raise RFIDReaderClientError(
                    f"Cache row {index} did not contain an EPC."
                )

            tags.append(tag)

        return CachedInventoryResult(
            statistics_frame=statistics_frame,
            duration_ms=statistics.duration_ms,
            read_count=statistics.read_count,
            expected_tag_count=statistics.tag_count,
            tags=tuple(tags),
        )


@dataclass(frozen=True)
class ActiveInventoryResult:
    start_frame: RFIDFrame
    stop_frame: RFIDFrame
    tag_frames: tuple[RFIDFrame, ...]
    tags: tuple[RFIDTagData, ...]
    total_tag_frames: int


class ActiveInventoryReaderClient:
    ACCEPTED_START_STATUSES = frozenset((0, 0x99))

    def __init__(
        self,
        *,
        address: int,
        scan_seconds: float = 3.0,
        time_function: Callable[[], float] = time.monotonic,
    ):
        if not 0 <= address <= 255:
            raise RFIDReaderClientError(
                "Reader address must be between 0 and 255."
            )

        if not 0 < scan_seconds <= 600:
            raise RFIDReaderClientError(
                "Active scan duration must be greater than 0 "
                "and no more than 600 seconds."
            )

        self._address = address
        self._scan_seconds = scan_seconds
        self._time = time_function

    def _validate_frame_address(self, frame: RFIDFrame) -> None:
        if frame.address != self._address:
            raise RFIDReaderClientError(
                "RFID response address does not match the configured reader."
            )

    def _process_frames(
        self,
        *,
        frames,
        start_frame,
        tag_frames,
        tags_by_epc,
    ):
        for frame in frames:
            self._validate_frame_address(frame)

            if frame.command == COMMAND_START_INVENTORY:
                if frame.status not in self.ACCEPTED_START_STATUSES:
                    raise RFIDReaderClientError(
                        "RFID reader returned unsupported active inventory "
                        f"start status {frame.status}."
                    )

                if start_frame is None:
                    start_frame = frame

                continue

            if frame.command != COMMAND_ACTIVE_TAG:
                continue

            if frame.status != 0:
                raise RFIDReaderClientError(
                    "RFID reader returned failure status "
                    f"{frame.status} for an active tag frame."
                )

            tag = parse_tag_data(frame.payload)

            if not tag.epc:
                raise RFIDReaderClientError(
                    "Active RFID tag frame did not contain an EPC."
                )

            tag_frames.append(frame)
            tags_by_epc.setdefault(tag.epc, tag)

        return start_frame

    def run(self, *, session) -> ActiveInventoryResult:
        start_request = build_frame(
            address=self._address,
            sequence=10,
            command=COMMAND_START_INVENTORY,
            payload=struct.pack(">I", 0x00000050),
        )
        stop_request = build_frame(
            address=self._address,
            sequence=11,
            command=COMMAND_STOP,
        )

        start_frame = None
        stop_frame = None
        tag_frames = []
        tags_by_epc = {}

        session.send(start_request)
        deadline = self._time() + self._scan_seconds

        try:
            while self._time() < deadline:
                frames = session.receive(
                    expected_commands=(
                        COMMAND_START_INVENTORY,
                        COMMAND_ACTIVE_TAG,
                    ),
                    maximum_reads=32,
                    timeout_returns_empty=True,
                )

                start_frame = self._process_frames(
                    frames=frames,
                    start_frame=start_frame,
                    tag_frames=tag_frames,
                    tags_by_epc=tags_by_epc,
                )
        finally:
            session.send(stop_request)

            stop_frames = session.receive(
                expected_commands=(COMMAND_STOP,),
                maximum_reads=32,
            )

            if len(stop_frames) != 1:
                raise RFIDReaderClientError(
                    "Expected exactly one active inventory stop response."
                )

            stop_frame = stop_frames[0]

            _require_success(
                stop_frame,
                expected_address=self._address,
                expected_command=COMMAND_STOP,
            )

        if start_frame is None:
            raise RFIDReaderClientError(
                "No active inventory start response was received."
            )

        return ActiveInventoryResult(
            start_frame=start_frame,
            stop_frame=stop_frame,
            tag_frames=tuple(tag_frames),
            tags=tuple(tags_by_epc.values()),
            total_tag_frames=len(tag_frames),
        )

