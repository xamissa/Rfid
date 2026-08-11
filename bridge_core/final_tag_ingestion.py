from dataclasses import dataclass
from hashlib import sha256
import json

from django.db import transaction

from bridge_core.ingestion import ingest_technical_reads
from bridge_core.models import (
    RawRFIDEvent,
    RFIDSession,
)
from bridge_core.reader_backends import TechnicalRFIDRead
from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    parse_tag_data,
)
from bridge_core.session_assignment_cycle import (
    run_active_session_assignment_cycle,
)


class FinalTagIngestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalTagIngestionResult:
    frame_count: int
    technical_read_count: int
    created_count: int
    duplicate_count: int
    assigned_count: int
    queued_count: int


def _normalized_session_key(session_key):
    value = str(session_key or "").strip()

    if not value:
        raise FinalTagIngestionError(
            "RFID session key cannot be empty."
        )

    if len(value) > 128:
        raise FinalTagIngestionError(
            "RFID session key exceeds 128 characters."
        )

    return value


def _event_key(*, session_key, epc):
    session_digest = sha256(
        session_key.encode("utf-8")
    ).hexdigest()[:24]

    epc_digest = sha256(
        epc.encode("ascii")
    ).hexdigest()[:24]

    return (
        f"final:{session_digest}:{epc_digest}"
    )


def _frame_to_technical_read(
    *,
    frame,
    device,
    session_key,
):
    if frame.command != COMMAND_ACTIVE_TAG:
        raise FinalTagIngestionError(
            "Final tag ingestion accepts active RFID tag frames only."
        )

    if frame.address != device.device_address:
        raise FinalTagIngestionError(
            "Active tag frame reader address mismatch."
        )

    if frame.status != 0:
        raise FinalTagIngestionError(
            "Active tag frame reports reader failure status "
            f"{frame.status}."
        )

    tag = parse_tag_data(frame.payload)

    epc = str(tag.epc or "").strip().upper()

    if not epc:
        raise FinalTagIngestionError(
            "Active tag frame does not contain an EPC."
        )

    try:
        epc.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FinalTagIngestionError(
            "EPC must contain ASCII characters only."
        ) from exc

    reader_event_key = _event_key(
        session_key=session_key,
        epc=epc,
    )

    raw_payload = json.dumps(
        {
            "source": "final_active_runtime",
            "session_key": session_key,
            "reader_code": device.code,
            "reader_address": frame.address,
            "frame": {
                "sequence": frame.sequence,
                "command": frame.command,
                "status": frame.status,
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

    return TechnicalRFIDRead(
        reader_event_key=reader_event_key,
        epc=epc,
        raw_payload=raw_payload,
    )


@transaction.atomic
def ingest_final_active_tag_frames(
    *,
    device,
    session_key,
    frames,
):
    """
    Persist final-runtime RFID observations locally before delivery.

    Idempotency is session + EPC:
    the same EPC observed repeatedly during one Odoo RFID session
    creates only one RawRFIDEvent.
    """
    session_key = _normalized_session_key(
        session_key
    )
    frames = tuple(frames)

    if not device.enabled:
        raise FinalTagIngestionError(
            "RFID reader device must be enabled."
        )

    try:
        session = (
            RFIDSession.objects
            .select_for_update()
            .get(
                device=device,
                external_session_key=session_key,
                status=RFIDSession.Status.ACTIVE,
            )
        )
    except RFIDSession.DoesNotExist as exc:
        raise FinalTagIngestionError(
            "No matching active local RFID session exists."
        ) from exc

    technical_reads = tuple(
        _frame_to_technical_read(
            frame=frame,
            device=device,
            session_key=session_key,
        )
        for frame in frames
    )

    ingestion = ingest_technical_reads(
        device=device,
        technical_reads=technical_reads,
    )

    assignment = (
        run_active_session_assignment_cycle(
            event_ids=ingestion.created_event_ids,
        )
    )

    if assignment.failed_count:
        raise FinalTagIngestionError(
            "One or more RFID events failed session assignment."
        )

    if assignment.unassigned_count:
        raise FinalTagIngestionError(
            "One or more RFID events remained unassigned."
        )

    if (
        assignment.assigned_count
        != ingestion.created_count
    ):
        raise FinalTagIngestionError(
            "Persisted and assigned RFID event counts differ."
        )

    # Integrity-check every deterministic event key, including duplicates.
    for technical_read in technical_reads:
        event = RawRFIDEvent.objects.get(
            device=device,
            reader_event_key=technical_read.reader_event_key,
        )

        if event.epc != technical_read.epc:
            raise FinalTagIngestionError(
                "Existing RFID event EPC does not match "
                "its deterministic event key."
            )

        if event.rfid_session_id != session.id:
            raise FinalTagIngestionError(
                "Existing RFID event belongs to a different session."
            )

        if event.queue_state not in (
            RawRFIDEvent.QueueState.QUEUED,
            RawRFIDEvent.QueueState.INFLIGHT,
            RawRFIDEvent.QueueState.RETRY,
            RawRFIDEvent.QueueState.SENT,
        ):
            raise FinalTagIngestionError(
                "Existing RFID event is in an invalid final-runtime "
                f"queue state: {event.queue_state}."
            )

    queued_count = RawRFIDEvent.objects.filter(
        device=device,
        rfid_session=session,
        queue_state=RawRFIDEvent.QueueState.QUEUED,
    ).count()

    return FinalTagIngestionResult(
        frame_count=len(frames),
        technical_read_count=len(
            technical_reads
        ),
        created_count=ingestion.created_count,
        duplicate_count=ingestion.duplicate_count,
        assigned_count=assignment.assigned_count,
        queued_count=queued_count,
    )
