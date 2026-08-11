from dataclasses import dataclass

from django.db.models import (
    OuterRef,
    Q,
    Subquery,
)
from django.utils import timezone

from bridge_core.delivery_cycle import (
    process_delivery_candidate,
)
from bridge_core.models import (
    DeliveryAttempt,
    RawRFIDEvent,
)


class FinalDeliveryCycleError(RuntimeError):
    pass


def select_final_delivery_candidates(
    *,
    reader_code,
    batch_size,
    now=None,
):
    if batch_size < 1:
        raise ValueError(
            "Final delivery batch size must be at least 1."
        )

    reader_code = str(
        reader_code or ""
    ).strip()

    if not reader_code:
        raise FinalDeliveryCycleError(
            "Final RFID reader code cannot be empty."
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
                latest_attempts.values(
                    "outcome"
                )[:1]
            ),
            latest_next_retry_at=Subquery(
                latest_attempts.values(
                    "next_retry_at"
                )[:1]
            ),
        )
        .filter(
            device__code=reader_code,
            rfid_session__isnull=False,
            reader_event_key__startswith="final:",
        )
        .filter(
            Q(
                queue_state=(
                    RawRFIDEvent.QueueState.QUEUED
                )
            )
            | Q(
                queue_state=(
                    RawRFIDEvent.QueueState.RETRY
                ),
                latest_attempt_outcome=(
                    DeliveryAttempt.Outcome.RETRY
                ),
                latest_next_retry_at__lte=now,
            )
        )
        .select_related(
            "device",
            "rfid_session",
        )
        .order_by(
            "received_at",
            "id",
        )[:batch_size]
    )

    return tuple(candidates)


@dataclass(frozen=True)
class FinalDeliveryCycleResult:
    selected_count: int
    processed_count: int
    sent_count: int
    retry_count: int
    rejected_count: int
    dead_count: int
    failed_count: int


def run_final_delivery_cycle(
    *,
    sender,
    reader_code,
    batch_size,
    max_delivery_attempts,
    retry_initial_seconds,
    retry_max_seconds,
    selection_function=select_final_delivery_candidates,
    processing_function=process_delivery_candidate,
):
    if max_delivery_attempts < 1:
        raise ValueError(
            "Maximum delivery attempts must be at least 1."
        )

    candidates = selection_function(
        reader_code=reader_code,
        batch_size=batch_size,
    )

    counters = {
        "processed": 0,
        "sent": 0,
        "retry": 0,
        "rejected": 0,
        "dead": 0,
        "failed": 0,
    }

    for event in candidates:
        try:
            result = processing_function(
                event=event,
                sender_backend=sender,
                max_delivery_attempts=(
                    max_delivery_attempts
                ),
                retry_initial_seconds=(
                    retry_initial_seconds
                ),
                retry_max_seconds=(
                    retry_max_seconds
                ),
            )
        except Exception:
            counters["failed"] += 1
            continue

        counters["processed"] += 1

        if result.outcome == DeliveryAttempt.Outcome.SENT:
            counters["sent"] += 1
        elif result.outcome == DeliveryAttempt.Outcome.RETRY:
            counters["retry"] += 1
        elif result.outcome == DeliveryAttempt.Outcome.REJECTED:
            counters["rejected"] += 1
        elif result.outcome == DeliveryAttempt.Outcome.DEAD:
            counters["dead"] += 1
        else:
            counters["failed"] += 1

    return FinalDeliveryCycleResult(
        selected_count=len(candidates),
        processed_count=counters["processed"],
        sent_count=counters["sent"],
        retry_count=counters["retry"],
        rejected_count=counters["rejected"],
        dead_count=counters["dead"],
        failed_count=counters["failed"],
    )
