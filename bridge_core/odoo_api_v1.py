import json
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPSHandler,
    Request,
    build_opener,
)


class OdooRFIDApiError(RuntimeError):
    """Base error for the final RFID v1 API client."""


class OdooRFIDApiConfigurationError(OdooRFIDApiError):
    """Raised when local API configuration is incomplete or unsafe."""


class OdooRFIDApiTransportError(OdooRFIDApiError):
    """Raised when HTTP transport to Odoo fails."""


class OdooRFIDApiProtocolError(OdooRFIDApiError):
    """Raised when Odoo returns an invalid or rejected JSON-RPC response."""


@dataclass(frozen=True)
class OdooRFIDApiResponse:
    result: dict
    response_code: int


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value or "").strip()

    if not normalized:
        raise OdooRFIDApiConfigurationError(
            f"{name} cannot be empty."
        )

    return normalized


def _build_endpoint_url(*, base_url: str, endpoint: str) -> str:
    normalized_base = _require_non_empty(
        "Odoo base URL",
        base_url,
    )
    normalized_endpoint = _require_non_empty(
        "Odoo endpoint",
        endpoint,
    )

    base_parts = urlsplit(normalized_base)

    if base_parts.scheme != "https":
        raise OdooRFIDApiConfigurationError(
            "Final RFID API requires an HTTPS Odoo base URL."
        )

    if not base_parts.netloc:
        raise OdooRFIDApiConfigurationError(
            "Odoo base URL must contain a hostname."
        )

    if base_parts.username or base_parts.password:
        raise OdooRFIDApiConfigurationError(
            "Credentials may not be embedded in the Odoo URL."
        )

    target = urljoin(
        normalized_base.rstrip("/") + "/",
        normalized_endpoint.lstrip("/"),
    )

    target_parts = urlsplit(target)

    if target_parts.scheme != "https" or not target_parts.netloc:
        raise OdooRFIDApiConfigurationError(
            "RFID API target must be a valid HTTPS URL."
        )

    return target


def _default_opener(*, verify_tls: bool):
    if verify_tls:
        context = ssl.create_default_context()
    else:
        context = ssl._create_unverified_context()

    return build_opener(
        HTTPSHandler(context=context)
    )


class OdooRFIDApiClient:
    HEARTBEAT_ENDPOINT = "/api/rfid/v1/heartbeat"
    COMMANDS_ENDPOINT = "/api/rfid/v1/commands"
    ACK_ENDPOINT = "/api/rfid/v1/ack"
    EVENTS_ENDPOINT = "/api/rfid/v1/events"

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        gateway_code: str,
        timeout_seconds: int = 10,
        verify_tls: bool = True,
        opener_factory=_default_opener,
    ):
        self._base_url = _require_non_empty(
            "Odoo base URL",
            base_url,
        )
        self._bearer_token = _require_non_empty(
            "Odoo bearer token",
            bearer_token,
        )
        self.gateway_code = _require_non_empty(
            "RFID gateway code",
            gateway_code,
        )

        if not 1 <= int(timeout_seconds) <= 300:
            raise OdooRFIDApiConfigurationError(
                "Odoo request timeout must be between 1 and 300 seconds."
            )

        self.timeout_seconds = int(timeout_seconds)
        self.verify_tls = bool(verify_tls)
        self._opener_factory = opener_factory

        # Validate the base URL immediately.
        _build_endpoint_url(
            base_url=self._base_url,
            endpoint=self.HEARTBEAT_ENDPOINT,
        )

    def _post(self, *, endpoint: str, params: dict) -> OdooRFIDApiResponse:
        target_url = _build_endpoint_url(
            base_url=self._base_url,
            endpoint=endpoint,
        )

        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": params,
            "id": None,
        }

        request = Request(
            target_url,
            data=json.dumps(
                payload,
                separators=(",", ":"),
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._bearer_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "ERPWeb-RFID-Bridge/1.0",
            },
            method="POST",
        )

        opener = self._opener_factory(
            verify_tls=self.verify_tls
        )

        try:
            with opener.open(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                response_code = int(response.getcode())
                raw_body = response.read()
        except HTTPError as exc:
            raise OdooRFIDApiTransportError(
                f"Odoo RFID API returned HTTP {exc.code}."
            ) from exc
        except URLError as exc:
            raise OdooRFIDApiTransportError(
                f"Odoo RFID API connection failed: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise OdooRFIDApiTransportError(
                f"Odoo RFID API transport failed: {exc}"
            ) from exc

        try:
            decoded = json.loads(
                raw_body.decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OdooRFIDApiProtocolError(
                "Odoo RFID API returned invalid JSON."
            ) from exc

        if not isinstance(decoded, dict):
            raise OdooRFIDApiProtocolError(
                "Odoo RFID API response must be a JSON object."
            )

        if decoded.get("error"):
            raise OdooRFIDApiProtocolError(
                "Odoo RFID API returned a JSON-RPC error."
            )

        result = decoded.get("result")

        if not isinstance(result, dict):
            raise OdooRFIDApiProtocolError(
                "Odoo RFID API response does not contain a valid result."
            )

        return OdooRFIDApiResponse(
            result=result,
            response_code=response_code,
        )

    def heartbeat(
        self,
        *,
        readers,
        software_version=None,
        reported_address=None,
        error=None,
    ):
        response = self._post(
            endpoint=self.HEARTBEAT_ENDPOINT,
            params={
                "gateway_code": self.gateway_code,
                "readers": list(readers),
                "software_version": software_version,
                "reported_address": reported_address,
                "error": error,
            },
        )

        return response.result

    def commands(self):
        response = self._post(
            endpoint=self.COMMANDS_ENDPOINT,
            params={
                "gateway_code": self.gateway_code,
            },
        )

        result = response.result

        if not result.get("ok"):
            raise OdooRFIDApiProtocolError(
                f"Odoo rejected command polling: {result.get('error') or 'unknown error'}"
            )

        commands = result.get("commands", [])

        if not isinstance(commands, list):
            raise OdooRFIDApiProtocolError(
                "Odoo commands response contains an invalid commands value."
            )

        return commands

    def ack(
        self,
        *,
        session_key,
        reader_code,
        command,
        revision,
        success=True,
        message=None,
    ):
        response = self._post(
            endpoint=self.ACK_ENDPOINT,
            params={
                "gateway_code": self.gateway_code,
                "session_key": _require_non_empty(
                    "RFID session key",
                    session_key,
                ),
                "reader_code": _require_non_empty(
                    "RFID reader code",
                    reader_code,
                ),
                "command": _require_non_empty(
                    "RFID command",
                    command,
                ),
                "revision": int(revision),
                "success": bool(success),
                "message": message,
            },
        )

        return response.result

    def events(
        self,
        *,
        session_key,
        reader_code,
        events,
    ):
        response = self._post(
            endpoint=self.EVENTS_ENDPOINT,
            params={
                "gateway_code": self.gateway_code,
                "session_key": _require_non_empty(
                    "RFID session key",
                    session_key,
                ),
                "reader_code": _require_non_empty(
                    "RFID reader code",
                    reader_code,
                ),
                "events": list(events),
            },
        )

        return response.result
