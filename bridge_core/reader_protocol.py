from dataclasses import dataclass
import struct


SOF = 0xFE
MIN_FRAME_LENGTH = 9
MAX_FRAME_LENGTH = 65535

COMMAND_HANDSHAKE = 0x00
COMMAND_STOP = 0x7F
COMMAND_START_INVENTORY = 0x81
COMMAND_CACHE_TAG = 0x82
COMMAND_INVENTORY_STATISTICS = 0xF8
COMMAND_ACTIVE_TAG = 0xF9


class RFIDProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class RFIDFrame:
    address: int
    sequence: int
    command: int
    status: int
    payload: bytes
    raw_frame: bytes


@dataclass(frozen=True)
class RFIDTagData:
    protocol_type: int
    antenna: int | None = None
    pc: int | None = None
    epc: str = ""
    tid: str = ""
    count: int | None = None


@dataclass(frozen=True)
class InventoryStatistics:
    duration_ms: int
    read_count: int
    tag_count: int


def calculate_crc(data: bytes) -> int:
    crc = 0xFFFF

    for byte in data:
        crc ^= byte << 8

        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF

    return crc


def _require_byte(name: str, value: int) -> None:
    if not 0 <= value <= 255:
        raise RFIDProtocolError(
            f"{name} must be between 0 and 255."
        )


def build_frame(
    *,
    address: int,
    sequence: int,
    command: int,
    payload: bytes = b"",
    status: int = 0,
) -> bytes:
    _require_byte("address", address)
    _require_byte("sequence", sequence)
    _require_byte("command", command)
    _require_byte("status", status)

    payload = bytes(payload)
    total_length = MIN_FRAME_LENGTH + len(payload)

    if total_length > MAX_FRAME_LENGTH:
        raise RFIDProtocolError(
            "Frame exceeds the two-byte length field."
        )

    frame_without_crc = (
        bytes((SOF,))
        + struct.pack(
            ">HBBBB",
            total_length,
            address,
            sequence,
            command,
            status,
        )
        + payload
    )

    return frame_without_crc + struct.pack(
        ">H",
        calculate_crc(frame_without_crc),
    )


def parse_frame(raw_frame: bytes) -> RFIDFrame:
    raw_frame = bytes(raw_frame)

    if len(raw_frame) < MIN_FRAME_LENGTH:
        raise RFIDProtocolError(
            "Frame is shorter than nine bytes."
        )

    if raw_frame[0] != SOF:
        raise RFIDProtocolError(
            "Frame does not start with 0xFE."
        )

    declared_length = struct.unpack(
        ">H",
        raw_frame[1:3],
    )[0]

    if declared_length < MIN_FRAME_LENGTH:
        raise RFIDProtocolError(
            "Declared frame length is invalid."
        )

    if declared_length != len(raw_frame):
        raise RFIDProtocolError(
            "Declared frame length does not match received bytes."
        )

    expected_crc = struct.unpack(
        ">H",
        raw_frame[-2:],
    )[0]
    actual_crc = calculate_crc(raw_frame[:-2])

    if actual_crc != expected_crc:
        raise RFIDProtocolError(
            "Frame CRC validation failed."
        )

    return RFIDFrame(
        address=raw_frame[3],
        sequence=raw_frame[4],
        command=raw_frame[5],
        status=raw_frame[6],
        payload=raw_frame[7:-2],
        raw_frame=raw_frame,
    )


def parse_inventory_statistics(
    payload: bytes,
) -> InventoryStatistics:
    payload = bytes(payload)

    if len(payload) < 12:
        raise RFIDProtocolError(
            "Inventory statistics payload is shorter than 12 bytes."
        )

    duration_ms, read_count, tag_count = struct.unpack(
        ">iii",
        payload[:12],
    )

    return InventoryStatistics(
        duration_ms=duration_ms,
        read_count=read_count,
        tag_count=tag_count,
    )


def _take(
    payload: bytes,
    offset: int,
    length: int,
    field: str,
):
    end = offset + length

    if end > len(payload):
        raise RFIDProtocolError(
            f"Tag payload ended while reading {field}."
        )

    return payload[offset:end], end


def parse_tag_data(payload: bytes) -> RFIDTagData:
    payload = bytes(payload)

    if len(payload) < 5:
        raise RFIDProtocolError(
            "Tag payload is shorter than its mask and protocol fields."
        )

    mask, protocol_type = struct.unpack(
        ">IB",
        payload[:5],
    )
    offset = 5

    antenna = None
    pc = None
    epc = ""
    tid = ""
    count = None

    if mask & (1 << 0):
        raw, offset = _take(
            payload,
            offset,
            1,
            "antenna",
        )
        antenna = raw[0]

    if mask & (1 << 1):
        raw, offset = _take(
            payload,
            offset,
            2,
            "PC",
        )
        pc = struct.unpack(">h", raw)[0]

    if mask & (1 << 2):
        raw_length, offset = _take(
            payload,
            offset,
            1,
            "EPC length",
        )
        raw_epc, offset = _take(
            payload,
            offset,
            raw_length[0],
            "EPC",
        )
        epc = raw_epc.hex().upper()

    if mask & (1 << 3):
        raw_length, offset = _take(
            payload,
            offset,
            1,
            "TID length",
        )
        raw_tid, offset = _take(
            payload,
            offset,
            raw_length[0],
            "TID",
        )
        tid = raw_tid.hex().upper()

    if mask & (1 << 4):
        raw, offset = _take(
            payload,
            offset,
            4,
            "read count",
        )
        count = struct.unpack(">i", raw)[0]

    return RFIDTagData(
        protocol_type=protocol_type,
        antenna=antenna,
        pc=pc,
        epc=epc,
        tid=tid,
        count=count,
    )


class FrameStreamDecoder:
    def __init__(self):
        self._buffer = bytearray()

    @property
    def pending_bytes(self) -> bytes:
        return bytes(self._buffer)

    def feed(
        self,
        data: bytes,
    ) -> tuple[RFIDFrame, ...]:
        self._buffer.extend(data)
        frames = []

        while self._buffer:
            sof_position = self._buffer.find(SOF)

            if sof_position < 0:
                self._buffer.clear()
                break

            if sof_position:
                del self._buffer[:sof_position]

            if len(self._buffer) < 3:
                break

            declared_length = struct.unpack(
                ">H",
                self._buffer[1:3],
            )[0]

            if (
                declared_length < MIN_FRAME_LENGTH
                or declared_length > MAX_FRAME_LENGTH
            ):
                del self._buffer[0]
                continue

            if len(self._buffer) < declared_length:
                break

            candidate = bytes(
                self._buffer[:declared_length]
            )

            try:
                frame = parse_frame(candidate)
            except RFIDProtocolError:
                del self._buffer[0]
                continue

            del self._buffer[:declared_length]
            frames.append(frame)

        return tuple(frames)
