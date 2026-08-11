from dataclasses import dataclass


@dataclass(frozen=True)
class DeliverySenderResult:
    outcome: str
    response_code: str = ""
    error_kind: str = ""
    detail: str = ""

    def __post_init__(self):
        allowed_outcomes = {
            "sent",
            "retry",
            "rejected",
            "dead",
        }

        if self.outcome not in allowed_outcomes:
            raise ValueError(
                "Unsupported sender result outcome: "
                f"{self.outcome}"
            )


class DisabledSenderBackend:
    """Fail-closed sender backend that never contacts an external system."""

    def send_event(self, *, event):
        del event
        raise RuntimeError(
            "Delivery is disabled; no external contact is permitted."
        )


class OdooSenderBackend:
    def __init__(self, configuration):
        self.configuration = configuration

    def send_event(self, *, event):
        from bridge_core.odoo_sender import send_rfid_event

        result = send_rfid_event(
            configuration=self.configuration,
            event=event,
        )

        return DeliverySenderResult(
            outcome=result.outcome,
            response_code=result.response_code,
            error_kind=result.error_kind,
            detail=result.detail,
        )


def get_sender_backend(
    backend_name,
    *,
    configuration=None,
):
    normalized_name = backend_name.strip().lower()

    if normalized_name == "disabled":
        return DisabledSenderBackend()

    if normalized_name == "odoo":
        if configuration is None:
            raise ValueError(
                "Odoo sender requires operational configuration."
            )

        return OdooSenderBackend(
            configuration=configuration,
        )

    raise ValueError(f"Unsupported sender backend: {backend_name}")
