import base64
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from bridge_core.models import OperationalConfiguration
from bridge_core.odoo_inventory_count_poc import (
    OdooInventoryCountPocProtocolError,
    authenticate_odoo_session,
    create_session_opener,
)


class OdooConnectionConfigurationError(ValueError):
    """Raised when the connection test configuration is unsafe or incomplete."""


@dataclass(frozen=True)
class OdooConnectionTestResult:
    success: bool
    target_url: str
    response_code: str = ""
    error_kind: str = ""
    detail: str = ""


def build_connection_test_url(*, base_url, session_endpoint):
    normalized_base_url = base_url.strip()
    normalized_endpoint = session_endpoint.strip()

    if not normalized_base_url:
        raise OdooConnectionConfigurationError(
            "Odoo base URL is required."
        )

    if not normalized_endpoint:
        raise OdooConnectionConfigurationError(
            "Odoo session endpoint is required for the connection test."
        )

    base_parts = urlsplit(normalized_base_url)

    if base_parts.scheme not in {"http", "https"}:
        raise OdooConnectionConfigurationError(
            "Odoo base URL must use HTTP or HTTPS."
        )

    if not base_parts.netloc:
        raise OdooConnectionConfigurationError(
            "Odoo base URL must include a hostname."
        )

    if base_parts.username or base_parts.password:
        raise OdooConnectionConfigurationError(
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
        raise OdooConnectionConfigurationError(
            "Odoo connection-test URL must use HTTP or HTTPS."
        )

    if not target_parts.netloc:
        raise OdooConnectionConfigurationError(
            "Odoo connection-test URL must include a hostname."
        )

    if target_parts.username or target_parts.password:
        raise OdooConnectionConfigurationError(
            "Credentials may not be embedded in the connection-test URL."
        )

    return target_url


def build_authentication_headers(*, configuration):
    headers = {
        "Accept": "application/json",
        "User-Agent": "rfid-bridge-connection-test/1.0",
    }

    if configuration.odoo_database:
        headers["X-Odoo-Database"] = (
            configuration.odoo_database.strip()
        )

    method = configuration.odoo_authentication_method
    client_identifier = (
        configuration.odoo_client_identifier.strip()
    )

    if method == OperationalConfiguration.OdooAuthenticationMethod.NONE:
        return headers

    secret = configuration.get_odoo_secret()

    if not secret:
        raise OdooConnectionConfigurationError(
            "The selected authentication method requires a stored credential."
        )

    if (
        method
        == OperationalConfiguration.OdooAuthenticationMethod.BEARER_TOKEN
    ):
        headers["Authorization"] = f"Bearer {secret}"

        if client_identifier:
            headers["X-Client-ID"] = client_identifier

        return headers

    if method == OperationalConfiguration.OdooAuthenticationMethod.BASIC:
        if not client_identifier:
            raise OdooConnectionConfigurationError(
                "Basic authentication requires a client identifier."
            )

        raw_credentials = (
            f"{client_identifier}:{secret}".encode("utf-8")
        )
        encoded_credentials = base64.b64encode(
            raw_credentials
        ).decode("ascii")

        headers["Authorization"] = (
            f"Basic {encoded_credentials}"
        )
        return headers

    if method == OperationalConfiguration.OdooAuthenticationMethod.API_KEY:
        headers["X-API-Key"] = secret

        if client_identifier:
            headers["X-Client-ID"] = client_identifier

        return headers

    raise OdooConnectionConfigurationError(
        f"Unsupported Odoo authentication method: {method}"
    )


def execute_odoo_connection_test(
    *,
    configuration,
    allow_contact,
    opener_factory=create_session_opener,
):
    if not allow_contact:
        raise PermissionError(
            "Odoo contact is disabled by the gateway configuration."
        )

    if (
        configuration.odoo_authentication_method
        != OperationalConfiguration.OdooAuthenticationMethod.ODOO_SESSION
    ):
        raise OdooConnectionConfigurationError(
            "The controlled Odoo connection test requires "
            "Odoo username and password session authentication."
        )

    target_url = build_connection_test_url(
        base_url=configuration.odoo_base_url,
        session_endpoint=configuration.odoo_session_endpoint,
    )

    timeout = configuration.odoo_request_timeout_seconds

    if not 1 <= timeout <= 300:
        raise OdooConnectionConfigurationError(
            "Odoo timeout must be between 1 and 300 seconds."
        )

    if not configuration.odoo_database.strip():
        raise OdooConnectionConfigurationError(
            "Odoo database is required."
        )

    if not configuration.odoo_client_identifier.strip():
        raise OdooConnectionConfigurationError(
            "Odoo login username is required."
        )

    if not configuration.has_odoo_secret:
        raise OdooConnectionConfigurationError(
            "Odoo login password is required."
        )

    opener = opener_factory(
        verify_tls=configuration.odoo_verify_tls
    )

    try:
        authenticate_odoo_session(
            configuration=configuration,
            opener=opener,
        )

        return OdooConnectionTestResult(
            success=True,
            target_url=target_url,
            response_code="200",
            detail=(
                "Odoo session authentication succeeded. "
                "No RFID event or inventory request was delivered."
            ),
        )
    except HTTPError as exc:
        return OdooConnectionTestResult(
            success=False,
            target_url=target_url,
            response_code=str(exc.code),
            error_kind="HTTPError",
            detail=(
                "Odoo was reached, but session authentication "
                "was rejected."
            ),
        )
    except URLError as exc:
        reason = getattr(exc, "reason", exc)

        return OdooConnectionTestResult(
            success=False,
            target_url=target_url,
            error_kind=type(reason).__name__,
            detail=(
                "The configured Odoo environment could not be reached."
            ),
        )
    except OdooInventoryCountPocProtocolError as exc:
        return OdooConnectionTestResult(
            success=False,
            target_url=target_url,
            error_kind=type(exc).__name__,
            detail=str(exc),
        )

