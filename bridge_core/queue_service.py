from dataclasses import dataclass

from django.db import transaction

from bridge_core.models import RawRFIDEvent
from bridge_core.queue_policy import validate_queue_transition


@dataclass(frozen=True)
class QueueTransitionResult:
    event_id: object
    previous_state: str
    current_state: str


def transition_event_queue_state(
    *,
    event_id,
    expected_state,
    target_state,
):
    with transaction.atomic():
        event = (
            RawRFIDEvent.objects
            .select_for_update()
            .get(event_id=event_id)
        )

        if event.queue_state != expected_state:
            raise ValueError(
                "Stale queue state expectation: "
                f"expected {expected_state}, "
                f"found {event.queue_state}"
            )

        validate_queue_transition(
            current_state=event.queue_state,
            target_state=target_state,
        )

        previous_state = event.queue_state
        event.queue_state = target_state
        event.save(
            update_fields=(
                "queue_state",
                "updated_at",
            ),
        )

        return QueueTransitionResult(
            event_id=event.event_id,
            previous_state=previous_state,
            current_state=event.queue_state,
        )
