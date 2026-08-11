from hashlib import sha256
import json

from bridge_core.reader_backends import TechnicalRFIDRead
from bridge_core.reader_client import (
    ActiveInventoryResult,
    CachedInventoryResult,
)


class RFIDResultAdapterError(ValueError):
    pass


def _normalize_scan_id(scan_id: str) -> str:
    normalized = str(scan_id).strip()

    if not normalized:
        raise RFIDResultAdapterError(
            "Scan ID cannot be empty."
        )

    if len(normalized) > 128:
        raise RFIDResultAdapterError(
            "Scan ID cannot exceed 128 characters."
        )

    return normalized


def _event_key(
    *,
    scan_id: str,
    row_index: int,
    epc: str,
) -> str:
    scan_digest = sha256(
        scan_id.encode("utf-8")
    ).hexdigest()[:24]
    epc_digest = sha256(
        epc.encode("ascii")
    ).hexdigest()[:16]

    return (
        f"cached:{scan_digest}:"
        f"{row_index}:{epc_digest}"
    )


def cached_inventory_to_technical_reads(
    *,
    result: CachedInventoryResult,
    scan_id: str,
) -> tuple[TechnicalRFIDRead, ...]:
    normalized_scan_id = _normalize_scan_id(scan_id)

    if result.expected_tag_count != len(result.tags):
        raise RFIDResultAdapterError(
            "Cached inventory tag count does not match "
            "the expected tag count."
        )

    technical_reads = []

    for row_index, tag in enumerate(result.tags):
        epc = tag.epc.strip().upper()

        if not epc:
            raise RFIDResultAdapterError(
                f"Cache row {row_index} does not contain an EPC."
            )

        raw_payload = json.dumps(
            {
                "source": "cached_inventory",
                "scan_id": normalized_scan_id,
                "row_index": row_index,
                "statistics": {
                    "duration_ms": result.duration_ms,
                    "read_count": result.read_count,
                    "expected_tag_count": (
                        result.expected_tag_count
                    ),
                },
                "tag": {
                    "protocol_type": tag.protocol_type,
                    "antenna": tag.antenna,
                    "pc": tag.pc,
                    "epc": epc,
                    "tid": tag.tid,
                    "count": tag.count,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        technical_reads.append(
            TechnicalRFIDRead(
                reader_event_key=_event_key(
                    scan_id=normalized_scan_id,
                    row_index=row_index,
                    epc=epc,
                ),
                epc=epc,
                raw_payload=raw_payload,
            )
        )

    return tuple(technical_reads)


def _active_event_key(
    *,
    scan_id: str,
    epc: str,
) -> str:
    scan_digest = sha256(
        scan_id.encode("utf-8")
    ).hexdigest()[:24]
    epc_digest = sha256(
        epc.encode("ascii")
    ).hexdigest()[:24]

    return f"active:{scan_digest}:{epc_digest}"


def active_inventory_to_technical_reads(
    *,
    result: ActiveInventoryResult,
    scan_id: str,
) -> tuple[TechnicalRFIDRead, ...]:
    normalized_scan_id = _normalize_scan_id(scan_id)

    technical_reads = []

    for tag in result.tags:
        epc = tag.epc.strip().upper()

        if not epc:
            raise RFIDResultAdapterError(
                "Active inventory tag does not contain an EPC."
            )

        raw_payload = json.dumps(
            {
                "source": "active_inventory",
                "scan_id": normalized_scan_id,
                "active_inventory": {
                    "start_status": result.start_frame.status,
                    "stop_status": result.stop_frame.status,
                    "total_tag_frames": result.total_tag_frames,
                    "unique_tag_count": len(result.tags),
                },
                "tag": {
                    "protocol_type": tag.protocol_type,
                    "antenna": tag.antenna,
                    "pc": tag.pc,
                    "epc": epc,
                    "tid": tag.tid,
                    "count": tag.count,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        technical_reads.append(
            TechnicalRFIDRead(
                reader_event_key=_active_event_key(
                    scan_id=normalized_scan_id,
                    epc=epc,
                ),
                epc=epc,
                raw_payload=raw_payload,
            )
        )

    return tuple(technical_reads)

