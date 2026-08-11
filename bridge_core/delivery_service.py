from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from bridge_core.models import (
    DeliveryAttempt,
    RawRFIDEvent,
)
from bridge_core.queue_policy import (
    calculate_retry_delay_seconds,
    validate_queue_transition,
)


@dataclass(frozen=True)
class DeliveryAttemptStartResult:
    event_id: object
    attempt_id: int
    attempt_number: int
    previous_state: str
    current_state: str


def start_delivery_attempt(
    *,
    event_id,
    expected_state,
    max_delivery_attempts,
):
    if max_delivery_attempts < 1:
        raise ValueError(
            "Maximum delivery attempts must be at least 1."
        )

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

        if event.queue_state not in (
            RawRFIDEvent.QueueState.QUEUED,
            RawRFIDEvent.QueueState.RETRY,
        ):
            raise ValueError(
                "Delivery attempt cannot start from queue state: "
                f"{event.queue_state}"
            )

        previous_attempt = (
            DeliveryAttempt.objects
            .filter(event=event)
            .order_by("-attempt_number")
            .first()
        )

        next_attempt_number = (
            previous_attempt.attempt_number + 1
            if previous_attempt is not None
            else 1
        )

        if next_attempt_number > max_delivery_attempts:
            raise ValueError(
                "Maximum delivery attempts reached: "
                f"{max_delivery_attempts}"
            )

        validate_queue_transition(
            current_state=event.queue_state,
            target_state=RawRFIDEvent.QueueState.INFLIGHT,
        )

        previous_state = event.queue_state
        event.queue_state = RawRFIDEvent.QueueState.INFLIGHT
        event.save(
            update_fields=(
                "queue_state",
                "updated_at",
            ),
        )

        attempt = DeliveryAttempt.objects.create(
            event=event,
            attempt_number=next_attempt_number,
            outcome=DeliveryAttempt.Outcome.STARTED,
        )

        return DeliveryAttemptStartResult(
            event_id=event.event_id,
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            previous_state=previous_state,
            current_state=event.queue_state,
        )


@dataclass(frozen=True)
class DeliveryAttemptCompletionResult:
    event_id: object
    attempt_id: int
    attempt_number: int
    outcome: str
    previous_state: str
    current_state: str
    completed_at: object
    next_retry_at: object


def complete_delivery_attempt(
    *,
    event_id,
    attempt_id,
    outcome,
    response_code="",
    error_kind="",
    detail="",
    retry_initial_seconds=None,
    retry_max_seconds=None,
):
    allowed_outcomes = {
        DeliveryAttempt.Outcome.SENT,
        DeliveryAttempt.Outcome.RETRY,
        DeliveryAttempt.Outcome.REJECTED,
        DeliveryAttempt.Outcome.DEAD,
    }

    if outcome not in allowed_outcomes:
        raise ValueError(
            f"Unsupported delivery completion outcome: {outcome}"
        )

    if outcome == DeliveryAttempt.Outcome.RETRY:
        if retry_initial_seconds is None:
            raise ValueError(
                "Retry completion requires retry_initial_seconds."
            )

        if retry_max_seconds is None:
            raise ValueError(
                "Retry completion requires retry_max_seconds."
            )

    with transaction.atomic():
        event = (
            RawRFIDEvent.objects
            .select_for_update()
            .get(event_id=event_id)
        )

        if event.queue_state != RawRFIDEvent.QueueState.INFLIGHT:
            raise ValueError(
                "Delivery attempt can only complete while event is "
                f"inflight; found {event.queue_state}"
            )

        attempt = (
            DeliveryAttempt.objects
            .select_for_update()
            .get(
                id=attempt_id,
                event=event,
            )
        )

        if attempt.outcome != DeliveryAttempt.Outcome.STARTED:
            raise ValueError(
                "Delivery attempt is already completed with outcome: "
                f"{attempt.outcome}"
            )

        outcome_to_queue_state = {
            DeliveryAttempt.Outcome.SENT: (
                RawRFIDEvent.QueueState.SENT
            ),
            DeliveryAttempt.Outcome.RETRY: (
                RawRFIDEvent.QueueState.RETRY
            ),
            DeliveryAttempt.Outcome.REJECTED: (
                RawRFIDEvent.QueueState.REJECTED
            ),
            DeliveryAttempt.Outcome.DEAD: (
                RawRFIDEvent.QueueState.DEAD
            ),
        }

        target_state = outcome_to_queue_state[outcome]

        validate_queue_transition(
            current_state=event.queue_state,
            target_state=target_state,
        )

        completed_at = timezone.now()
        next_retry_at = None

        if outcome == DeliveryAttempt.Outcome.RETRY:
            retry_delay_seconds = calculate_retry_delay_seconds(
                attempt_number=attempt.attempt_number,
                initial_seconds=retry_initial_seconds,
                maximum_seconds=retry_max_seconds,
            )
            next_retry_at = completed_at + timedelta(
                seconds=retry_delay_seconds,
            )

        previous_state = event.queue_state
        event.queue_state = target_state
        event.save(
            update_fields=(
                "queue_state",
                "updated_at",
            ),
        )

        attempt.outcome = outcome
        attempt.response_code = response_code
        attempt.error_kind = error_kind
        attempt.detail = detail
        attempt.completed_at = completed_at
        attempt.next_retry_at = next_retry_at
        attempt.save(
            update_fields=(
                "outcome",
                "response_code",
                "error_kind",
                "detail",
                "completed_at",
                "next_retry_at",
            ),
        )

        return DeliveryAttemptCompletionResult(
            event_id=event.event_id,
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            outcome=attempt.outcome,
            previous_state=previous_state,
            current_state=event.queue_state,
            completed_at=attempt.completed_at,
            next_retry_at=attempt.next_retry_at,
        )
