from dataclasses import dataclass

from bridge_core.models import (
    DeliveryAttempt,
    RawRFIDEvent,
)
from bridge_core.odoo_api_v1 import (
    OdooRFIDApiError,
)


class FinalOdooEventSenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalOdooEventSenderResult:
    outcome: str
    response_code: str = ""
    error_kind: str = ""
    detail: str = ""


class FinalOdooEventSender:
    """
    Deliver one durable final-runtime RFID observation through the
    Odoo RFID v1 bearer-auth API.

    Odoo event UUID is the locally persisted RawRFIDEvent.event_id.
    This makes retries idempotent across network failures.
    """

    ACCEPTED_OUTCOMES = {
        "accepted",
        "duplicate",
    }

    def __init__(
        self,
        *,
        api_client,
        reader_code,
    ):
        self.api_client = api_client
        self.reader_code = str(
            reader_code or ""
        ).strip()

        if not self.reader_code:
            raise FinalOdooEventSenderError(
                "Final RFID reader code cannot be empty."
            )

    def send_event(self, *, event):
        if not isinstance(event, RawRFIDEvent):
            raise FinalOdooEventSenderError(
                "Final sender requires a RawRFIDEvent."
            )

        if event.device.code != self.reader_code:
            raise FinalOdooEventSenderError(
                "RFID event belongs to a different reader."
            )

        if not event.reader_event_key.startswith("final:"):
            raise FinalOdooEventSenderError(
                "Non-final RFID event cannot use final delivery."
            )

        if event.rfid_session_id is None:
            raise FinalOdooEventSenderError(
                "Final RFID event has no assigned session."
            )

        session = event.rfid_session

        if session.device_id != event.device_id:
            raise FinalOdooEventSenderError(
                "RFID event/session reader mismatch."
            )

        session_key = str(
            session.external_session_key or ""
        ).strip()

        if not session_key:
            raise FinalOdooEventSenderError(
                "RFID event session key is empty."
            )

        epc = str(
            event.epc or ""
        ).strip().upper()

        if not epc:
            raise FinalOdooEventSenderError(
                "RFID event EPC is empty."
            )

        event_uuid = str(event.event_id)

        payload = {
            "event_uuid": event_uuid,
            "epc": epc,
            "seen_at": event.received_at.isoformat(),
            "raw_read_count": 1,
        }

        try:
            result = self.api_client.events(
                session_key=session_key,
                reader_code=self.reader_code,
                events=[payload],
            )
        except OdooRFIDApiError as exc:
            return FinalOdooEventSenderResult(
                outcome=DeliveryAttempt.Outcome.RETRY,
                error_kind=type(exc).__name__,
                detail=str(exc),
            )

        if not isinstance(result, dict):
            return FinalOdooEventSenderResult(
                outcome=DeliveryAttempt.Outcome.RETRY,
                error_kind="InvalidResponse",
                detail="Odoo event response is not an object.",
            )

        if not result.get("ok"):
            return FinalOdooEventSenderResult(
                outcome=DeliveryAttempt.Outcome.RETRY,
                error_kind="OdooRejectedRequest",
                detail=str(
                    result.get("error")
                    or "Odoo rejected RFID event request."
                ),
            )

        results = result.get("results")

        if not isinstance(results, list):
            return FinalOdooEventSenderResult(
                outcome=DeliveryAttempt.Outcome.RETRY,
                error_kind="InvalidResponse",
                detail="Odoo event response has no valid results list.",
            )

        matches = [
            item
            for item in results
            if (
                isinstance(item, dict)
                and str(
                    item.get("event_uuid") or ""
                ) == event_uuid
            )
        ]

        if len(matches) != 1:
            return FinalOdooEventSenderResult(
                outcome=DeliveryAttempt.Outcome.RETRY,
                error_kind="EventCorrelationError",
                detail=(
                    "Odoo event response did not contain exactly "
                    "one matching event UUID."
                ),
            )

        item = matches[0]
        outcome = str(
            item.get("outcome") or ""
        ).strip().lower()

        if outcome in self.ACCEPTED_OUTCOMES:
            return FinalOdooEventSenderResult(
                outcome=DeliveryAttempt.Outcome.SENT,
                response_code=outcome,
                detail=(
                    "RFID event accepted by Odoo."
                    if outcome == "accepted"
                    else "RFID event already existed in Odoo."
                ),
            )

        return FinalOdooEventSenderResult(
            outcome=DeliveryAttempt.Outcome.REJECTED,
            response_code=outcome,
            error_kind="OdooEventRejected",
            detail=str(
                item.get("error")
                or item.get("message")
                or f"Odoo event outcome: {outcome or 'unknown'}"
            ),
        )
