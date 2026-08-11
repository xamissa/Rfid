from dataclasses import dataclass

from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from bridge_core.delivery_service import (
    complete_delivery_attempt,
    start_delivery_attempt,
)
from bridge_core.models import DeliveryAttempt, RawRFIDEvent
from bridge_core.sender_backends import DeliverySenderResult


def select_delivery_candidates(*, batch_size, now=None):
    if batch_size < 1:
        raise ValueError(
            "Delivery candidate batch size must be at least 1."
        )

    if now is None:
        now = timezone.now()

    latest_attempts = (
        DeliveryAttempt.objects
        .filter(event_id=OuterRef("pk"))
        .order_by("-attempt_number")
    )

    candidates = (
        RawRFIDEvent.objects
        .annotate(
            latest_attempt_outcome=Subquery(
                latest_attempts.values("outcome")[:1]
            ),
            latest_next_retry_at=Subquery(
                latest_attempts.values("next_retry_at")[:1]
            ),
        )
        .filter(
            Q(queue_state=RawRFIDEvent.QueueState.QUEUED)
            | Q(
                queue_state=RawRFIDEvent.QueueState.RETRY,
                latest_attempt_outcome=DeliveryAttempt.Outcome.RETRY,
                latest_next_retry_at__lte=now,
            )
        )
        .select_related("device")
        .order_by("received_at", "id")[:batch_size]
    )

    return tuple(candidates)


@dataclass(frozen=True)
class SingleEventDeliveryResult:
    event_id: object
    attempt_id: int
    attempt_number: int
    outcome: str
    current_state: str
    next_retry_at: object


def process_delivery_candidate(
    *,
    event,
    sender_backend,
    max_delivery_attempts,
    retry_initial_seconds,
    retry_max_seconds,
    start_function=start_delivery_attempt,
    complete_function=complete_delivery_attempt,
):
    start_result = start_function(
        event_id=event.event_id,
        expected_state=event.queue_state,
        max_delivery_attempts=max_delivery_attempts,
    )

    try:
        sender_result = sender_backend.send_event(event=event)
    except Exception as exc:
        sender_result = DeliverySenderResult(
            outcome=DeliveryAttempt.Outcome.RETRY,
            error_kind=type(exc).__name__,
            detail=str(exc),
        )

    completion_result = complete_function(
        event_id=event.event_id,
        attempt_id=start_result.attempt_id,
        outcome=sender_result.outcome,
        response_code=sender_result.response_code,
        error_kind=sender_result.error_kind,
        detail=sender_result.detail,
        retry_initial_seconds=retry_initial_seconds,
        retry_max_seconds=retry_max_seconds,
    )

    return SingleEventDeliveryResult(
        event_id=completion_result.event_id,
        attempt_id=completion_result.attempt_id,
        attempt_number=completion_result.attempt_number,
        outcome=completion_result.outcome,
        current_state=completion_result.current_state,
        next_retry_at=completion_result.next_retry_at,
    )


@dataclass(frozen=True)
class BatchDeliveryCycleResult:
    selected_count: int
    processed_count: int
    sent_count: int
    retry_count: int
    rejected_count: int
    dead_count: int
    failed_count: int


def run_batch_delivery_cycle(
    *,
    sender_backend,
    batch_size,
    max_delivery_attempts,
    retry_initial_seconds,
    retry_max_seconds,
    selection_function=select_delivery_candidates,
    processing_function=process_delivery_candidate,
):
    if batch_size < 1:
        raise ValueError(
            "Delivery batch size must be at least 1."
        )

    if max_delivery_attempts < 1:
        raise ValueError(
            "Maximum delivery attempts must be at least 1."
        )

    if retry_initial_seconds < 1:
        raise ValueError(
            "Initial retry delay must be at least 1 second."
        )

    if retry_max_seconds < retry_initial_seconds:
        raise ValueError(
            "Maximum retry delay cannot be less than initial retry delay."
        )

    candidates = selection_function(batch_size=batch_size)

    processed_count = 0
    sent_count = 0
    retry_count = 0
    rejected_count = 0
    dead_count = 0
    failed_count = 0

    outcome_counters = {
        DeliveryAttempt.Outcome.SENT: "sent",
        DeliveryAttempt.Outcome.RETRY: "retry",
        DeliveryAttempt.Outcome.REJECTED: "rejected",
        DeliveryAttempt.Outcome.DEAD: "dead",
    }

    for event in candidates:
        try:
            result = processing_function(
                event=event,
                sender_backend=sender_backend,
                max_delivery_attempts=max_delivery_attempts,
                retry_initial_seconds=retry_initial_seconds,
                retry_max_seconds=retry_max_seconds,
            )
        except Exception:
            failed_count += 1
            continue

        processed_count += 1
        counter_name = outcome_counters.get(result.outcome)

        if counter_name == "sent":
            sent_count += 1
        elif counter_name == "retry":
            retry_count += 1
        elif counter_name == "rejected":
            rejected_count += 1
        elif counter_name == "dead":
            dead_count += 1
        else:
            raise ValueError(
                "Unsupported processed delivery outcome: "
                f"{result.outcome}"
            )

    return BatchDeliveryCycleResult(
        selected_count=len(candidates),
        processed_count=processed_count,
        sent_count=sent_count,
        retry_count=retry_count,
        rejected_count=rejected_count,
        dead_count=dead_count,
        failed_count=failed_count,
    )
