import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request

from bridge_core.models import OperationalConfiguration
from bridge_core.odoo_inventory_count_poc import (
    build_odoo_url,
    build_json_request,
    create_session_opener,
    authenticate_odoo_session,
)


@dataclass(frozen=True)
class OdooEventSendResult:
    outcome: str
    response_code: str = ""
    error_kind: str = ""
    detail: str = ""


def send_rfid_event(*, configuration, event, opener_factory=create_session_opener):
    if not configuration.odoo_integration_enabled:
        raise ValueError(
            "Odoo integration is disabled."
        )

    if (
        configuration.odoo_authentication_method
        != OperationalConfiguration.OdooAuthenticationMethod.ODOO_SESSION
    ):
        raise ValueError(
            "RFID event delivery requires Odoo session authentication."
        )

    target_url = build_odoo_url(
        base_url=configuration.odoo_base_url,
        endpoint=configuration.odoo_event_endpoint,
    )

    opener = opener_factory(
        verify_tls=configuration.odoo_verify_tls
    )

    authenticate_odoo_session(
        configuration=configuration,
        opener=opener,
    )

    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "event_id": str(event.event_id),
            "session_key": (
                event.rfid_session.external_session_key
                if event.rfid_session
                else None
            ),
            "reader_event_key": event.reader_event_key,
            "epc": event.epc,
            "reader": event.device.code,
            "received_at": event.received_at.isoformat(),
        },
        "id": None,
    }

    request = build_json_request(
        target_url=target_url,
        payload=payload,
    )

    try:
        with opener.open(
            request,
            timeout=configuration.odoo_request_timeout_seconds,
        ) as response:
            body = response.read()
            response_code = str(response.getcode())

        result = json.loads(body.decode("utf-8"))

        if result.get("error"):
            return OdooEventSendResult(
                outcome="rejected",
                response_code=response_code,
                detail="Odoo rejected RFID event.",
            )

        return OdooEventSendResult(
            outcome="sent",
            response_code=response_code,
            detail="RFID event delivered to Odoo.",
        )

    except HTTPError as exc:
        return OdooEventSendResult(
            outcome="retry",
            response_code=str(exc.code),
            error_kind="HTTPError",
            detail="Odoo HTTP error during RFID event delivery.",
        )

    except URLError as exc:
        return OdooEventSendResult(
            outcome="retry",
            error_kind="URLError",
            detail=str(exc),
        )
