from dataclasses import dataclass

from config.environment import ConfigurationError


@dataclass(frozen=True)
class FinalRFIDRuntimeConfiguration:
    odoo_base_url: str
    bearer_token: str
    gateway_code: str
    reader_code: str
    poll_seconds: float
    request_timeout_seconds: int
    verify_tls: bool


def _required(configuration, key):
    value = str(
        configuration.get(key, "")
    ).strip()

    if not value:
        raise ConfigurationError(
            f"Required final RFID configuration is missing: {key}"
        )

    return value


def _bool(configuration, key, default):
    raw = str(
        configuration.get(
            key,
            "true" if default else "false",
        )
    ).strip().lower()

    if raw == "true":
        return True

    if raw == "false":
        return False

    raise ConfigurationError(
        f"Final RFID configuration must be true or false: {key}"
    )


def load_final_runtime_configuration(configuration):
    base_url = _required(
        configuration,
        "RFID_ODOO_BASE_URL",
    )

    bearer_token = _required(
        configuration,
        "RFID_ODOO_BEARER_TOKEN",
    )

    gateway_code = _required(
        configuration,
        "RFID_GATEWAY_CODE",
    )

    reader_code = str(
        configuration.get(
            "RFID_READER_CODE",
            "receiving-door-01",
        )
    ).strip()

    if not reader_code:
        raise ConfigurationError(
            "RFID_READER_CODE cannot be empty."
        )

    try:
        poll_seconds = float(
            configuration.get(
                "RFID_CONTROL_POLL_SECONDS",
                "1.0",
            )
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "RFID_CONTROL_POLL_SECONDS must be numeric."
        ) from exc

    if not 0.1 <= poll_seconds <= 60:
        raise ConfigurationError(
            "RFID_CONTROL_POLL_SECONDS must be between 0.1 and 60."
        )

    try:
        timeout = int(
            configuration.get(
                "RFID_ODOO_REQUEST_TIMEOUT_SECONDS",
                "10",
            )
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "RFID_ODOO_REQUEST_TIMEOUT_SECONDS must be an integer."
        ) from exc

    if not 1 <= timeout <= 300:
        raise ConfigurationError(
            "RFID_ODOO_REQUEST_TIMEOUT_SECONDS must be between 1 and 300."
        )

    verify_tls = _bool(
        configuration,
        "RFID_ODOO_VERIFY_TLS",
        True,
    )

    return FinalRFIDRuntimeConfiguration(
        odoo_base_url=base_url,
        bearer_token=bearer_token,
        gateway_code=gateway_code,
        reader_code=reader_code,
        poll_seconds=poll_seconds,
        request_timeout_seconds=timeout,
        verify_tls=verify_tls,
    )
