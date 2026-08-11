from dataclasses import dataclass

from bridge_core.models import RawRFIDEvent


class FinalDeliveryBarrierError(RuntimeError):
    pass


class FinalDeliveryBarrierPending(FinalDeliveryBarrierError):
    pass


class FinalDeliveryBarrierFailed(FinalDeliveryBarrierError):
    pass


@dataclass(frozen=True)
class FinalDeliveryBarrierResult:
    total_count: int
    sent_count: int
    pending_count: int
    failed_count: int


def inspect_final_session_delivery(
    *,
    session_key,
    reader_code,
):
    session_key = str(session_key or "").strip()
    reader_code = str(reader_code or "").strip()

    if not session_key:
        raise FinalDeliveryBarrierError(
            "STOP delivery barrier requires a session key."
        )

    if not reader_code:
        raise FinalDeliveryBarrierError(
            "STOP delivery barrier requires a reader code."
        )

    states = tuple(
        RawRFIDEvent.objects
        .filter(
            device__code=reader_code,
            rfid_session__external_session_key=session_key,
            reader_event_key__startswith="final:",
        )
        .values_list(
            "queue_state",
            flat=True,
        )
    )

    sent_count = sum(
        state == RawRFIDEvent.QueueState.SENT
        for state in states
    )

    failed_count = sum(
        state in {
            RawRFIDEvent.QueueState.REJECTED,
            RawRFIDEvent.QueueState.DEAD,
        }
        for state in states
    )

    pending_count = (
        len(states)
        - sent_count
        - failed_count
    )

    return FinalDeliveryBarrierResult(
        total_count=len(states),
        sent_count=sent_count,
        pending_count=pending_count,
        failed_count=failed_count,
    )


def require_final_session_delivery_complete(
    *,
    session_key,
    reader_code,
):
    result = inspect_final_session_delivery(
        session_key=session_key,
        reader_code=reader_code,
    )

    if result.failed_count:
        raise FinalDeliveryBarrierFailed(
            "STOP success ACK blocked because "
            f"{result.failed_count} final RFID event(s) "
            "are rejected or dead and require recovery."
        )

    if result.pending_count:
        raise FinalDeliveryBarrierPending(
            "STOP success ACK waiting for "
            f"{result.pending_count} final RFID event(s) "
            "to be delivered to Odoo."
        )

    return result
