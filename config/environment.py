from pathlib import Path


APP_ENV_FILE = Path("/etc/rfid_bridge/app.env")
SECRETS_ENV_FILE = Path("/etc/rfid_bridge/secrets.env")


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is invalid."""


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ConfigurationError(f"Required configuration file is missing: {path}")

    values: dict[str, str] = {}

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise ConfigurationError(
                f"Malformed configuration line in {path} at line {line_number}"
            )

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            raise ConfigurationError(
                f"Empty configuration key in {path} at line {line_number}"
            )

        if key in values:
            raise ConfigurationError(f"Duplicate configuration key in {path}: {key}")

        values[key] = value

    return values


def load_configuration() -> dict[str, str]:
    configuration = _load_env_file(APP_ENV_FILE)

    for key, value in _load_env_file(SECRETS_ENV_FILE).items():
        if key in configuration:
            raise ConfigurationError(
                f"Configuration key exists in both files: {key}"
            )

        configuration[key] = value

    return configuration


def require_value(configuration: dict[str, str], key: str) -> str:
    value = configuration.get(key, "").strip()

    if not value:
        raise ConfigurationError(f"Required configuration value is missing: {key}")

    return value


def require_bool(configuration: dict[str, str], key: str) -> bool:
    value = require_value(configuration, key).lower()

    if value == "true":
        return True

    if value == "false":
        return False

    raise ConfigurationError(
        f"Configuration value must be true or false: {key}"
    )
