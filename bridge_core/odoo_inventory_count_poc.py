import json
import ssl
from dataclasses import dataclass
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPSHandler,
    Request,
    build_opener,
)

from bridge_core.models import OperationalConfiguration


class OdooInventoryCountPocConfigurationError(ValueError):
    """Raised when the inventory validation configuration is invalid."""


class OdooInventoryCountPocProtocolError(RuntimeError):
    """Raised when Odoo returns an invalid JSON-RPC response."""


@dataclass(frozen=True)
class OdooInventoryCountPocResult:
    success: bool
    target_url: str
    response_code: str = ""
    total_counted: int | None = None
    unknown_rfid_tags: tuple[str, ...] = ()
    error_kind: str = ""
    detail: str = ""


def build_odoo_url(*, base_url: str, endpoint: str) -> str:
    normalized_base_url = base_url.strip()
    normalized_endpoint = endpoint.strip()

    if not normalized_base_url:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo base URL is required."
        )

    if not normalized_endpoint:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo endpoint is required."
        )

    base_parts = urlsplit(normalized_base_url)

    if base_parts.scheme not in {"http", "https"}:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo base URL must use HTTP or HTTPS."
        )

    if not base_parts.netloc:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo base URL must include a hostname."
        )

    if base_parts.username or base_parts.password:
        raise OdooInventoryCountPocConfigurationError(
            "Credentials may not be embedded in the Odoo URL."
        )

    endpoint_parts = urlsplit(normalized_endpoint)

    if endpoint_parts.scheme:
        target_url = normalized_endpoint
    else:
        target_url = urljoin(
            normalized_base_url.rstrip("/") + "/",
            normalized_endpoint.lstrip("/"),
        )

    target_parts = urlsplit(target_url)

    if target_parts.scheme not in {"http", "https"}:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo target URL must use HTTP or HTTPS."
        )

    if not target_parts.netloc:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo target URL must include a hostname."
        )

    if target_parts.username or target_parts.password:
        raise OdooInventoryCountPocConfigurationError(
            "Credentials may not be embedded in the target URL."
        )

    return target_url


def normalize_rfid_tags(rfid_tags) -> tuple[str, ...]:
    normalized_tags = []
    seen = set()

    for value in rfid_tags:
        normalized = str(value).strip().upper()

        if not normalized:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        normalized_tags.append(normalized)

    if not normalized_tags:
        raise OdooInventoryCountPocConfigurationError(
            "At least one RFID tag is required."
        )

    return tuple(normalized_tags)


def validate_poc_configuration(*, configuration) -> None:
    if not configuration.odoo_inventory_count_poc_enabled:
        raise OdooInventoryCountPocConfigurationError(
            "The inventory validation feature is disabled."
        )

    if (
        configuration.odoo_authentication_method
        != OperationalConfiguration
        .OdooAuthenticationMethod.ODOO_SESSION
    ):
        raise OdooInventoryCountPocConfigurationError(
            "The inventory validation feature requires Odoo session authentication."
        )

    if not configuration.odoo_base_url.strip():
        raise OdooInventoryCountPocConfigurationError(
            "Odoo base URL is required."
        )

    if not configuration.odoo_database.strip():
        raise OdooInventoryCountPocConfigurationError(
            "Odoo database is required."
        )

    if not configuration.odoo_client_identifier.strip():
        raise OdooInventoryCountPocConfigurationError(
            "Odoo login username is required."
        )

    if not configuration.has_odoo_secret:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo login password is required."
        )

    if not configuration.odoo_inventory_count_endpoint.strip():
        raise OdooInventoryCountPocConfigurationError(
            "Inventory-count endpoint is required."
        )

    location_id = configuration.odoo_inventory_count_location_id

    if location_id is None or location_id < 1:
        raise OdooInventoryCountPocConfigurationError(
            "A positive Odoo staging location ID is required."
        )

    timeout = configuration.odoo_request_timeout_seconds

    if not 1 <= timeout <= 300:
        raise OdooInventoryCountPocConfigurationError(
            "Odoo timeout must be between 1 and 300 seconds."
        )


def build_json_request(*, target_url: str, payload: dict) -> Request:
    body = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode("utf-8")

    return Request(
        target_url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "rfid-bridge-inventory-count-poc/1.0",
        },
        method="POST",
    )


def decode_json_response(response_body: bytes) -> dict:
    try:
        decoded = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OdooInventoryCountPocProtocolError(
            "Odoo returned an invalid JSON response."
        ) from exc

    if not isinstance(decoded, dict):
        raise OdooInventoryCountPocProtocolError(
            "Odoo returned a non-object JSON response."
        )

    return decoded


def extract_jsonrpc_result(response_payload: dict) -> dict:
    if response_payload.get("error"):
        error = response_payload["error"]

        if isinstance(error, dict):
            message = (
                error.get("data", {}).get("message")
                if isinstance(error.get("data"), dict)
                else None
            ) or error.get("message")
        else:
            message = None

        raise OdooInventoryCountPocProtocolError(
            message or "Odoo returned a JSON-RPC error."
        )

    result = response_payload.get("result")

    if not isinstance(result, dict):
        raise OdooInventoryCountPocProtocolError(
            "Odoo JSON-RPC response did not contain an object result."
        )

    return result


def create_session_opener(*, verify_tls: bool):
    cookie_jar = CookieJar()

    if verify_tls:
        context = ssl.create_default_context()
    else:
        context = ssl._create_unverified_context()

    opener = build_opener(
        HTTPCookieProcessor(cookie_jar),
        HTTPSHandler(context=context),
    )

    return opener


def authenticate_odoo_session(
    *,
    configuration,
    opener,
) -> str:
    authenticate_url = build_odoo_url(
        base_url=configuration.odoo_base_url,
        endpoint="/web/session/authenticate",
    )

    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "db": configuration.odoo_database.strip(),
            "login": (
                configuration.odoo_client_identifier.strip()
            ),
            "password": configuration.get_odoo_secret(),
        },
        "id": None,
    }

    request = build_json_request(
        target_url=authenticate_url,
        payload=payload,
    )

    timeout = configuration.odoo_request_timeout_seconds

    with opener.open(
        request,
        timeout=timeout,
    ) as response:
        response_payload = decode_json_response(
            response.read()
        )

    result = extract_jsonrpc_result(response_payload)
    uid = result.get("uid")

    if not isinstance(uid, int) or uid < 1:
        raise OdooInventoryCountPocProtocolError(
            "Odoo session authentication did not return a valid user ID."
        )

    return authenticate_url


def execute_inventory_count_poc(
    *,
    configuration,
    rfid_tags,
    allow_contact: bool,
    opener_factory=create_session_opener,
) -> OdooInventoryCountPocResult:
    if not allow_contact:
        raise PermissionError(
            "Odoo contact is disabled by the gateway configuration."
        )

    validate_poc_configuration(
        configuration=configuration
    )

    normalized_tags = normalize_rfid_tags(rfid_tags)

    target_url = build_odoo_url(
        base_url=configuration.odoo_base_url,
        endpoint=(
            configuration.odoo_inventory_count_endpoint
        ),
    )

    opener = opener_factory(
        verify_tls=configuration.odoo_verify_tls
    )

    try:
        authenticate_odoo_session(
            configuration=configuration,
            opener=opener,
        )

        request = build_json_request(
            target_url=target_url,
            payload={
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "location_id": (
                        configuration
                        .odoo_inventory_count_location_id
                    ),
                    "rfid_tags": list(normalized_tags),
                },
                "id": None,
            },
        )

        with opener.open(
            request,
            timeout=(
                configuration
                .odoo_request_timeout_seconds
            ),
        ) as response:
            response_code = str(response.getcode())
            response_payload = decode_json_response(
                response.read()
            )

        result = extract_jsonrpc_result(response_payload)

        if result.get("status") != "success":
            raise OdooInventoryCountPocProtocolError(
                "Odoo inventory-count response did not report success."
            )

        total_counted = result.get("total_counted")

        if not isinstance(total_counted, int):
            raise OdooInventoryCountPocProtocolError(
                "Odoo inventory-count response omitted total_counted."
            )

        unknown_tags = result.get(
            "unknown_rfid_tags",
            [],
        )

        if not isinstance(unknown_tags, list):
            raise OdooInventoryCountPocProtocolError(
                "Odoo inventory-count response contains invalid unknown tags."
            )

        normalized_unknown_tags = tuple(
            str(value).strip().upper()
            for value in unknown_tags
            if str(value).strip()
        )

        return OdooInventoryCountPocResult(
            success=True,
            target_url=target_url,
            response_code=response_code,
            total_counted=total_counted,
            unknown_rfid_tags=normalized_unknown_tags,
            detail=(
                "Odoo authenticated the POC session and processed "
                "the inventory-count request."
            ),
        )
    except HTTPError as exc:
        return OdooInventoryCountPocResult(
            success=False,
            target_url=target_url,
            response_code=str(exc.code),
            error_kind="HTTPError",
            detail=(
                "Odoo rejected the authenticated inventory-count request."
            ),
        )
    except URLError as exc:
        reason = getattr(exc, "reason", exc)

        return OdooInventoryCountPocResult(
            success=False,
            target_url=target_url,
            error_kind=type(reason).__name__,
            detail=(
                "The configured Odoo environment could not be reached."
            ),
        )
    except OdooInventoryCountPocProtocolError as exc:
        return OdooInventoryCountPocResult(
            success=False,
            target_url=target_url,
            error_kind=type(exc).__name__,
            detail=str(exc),
        )
