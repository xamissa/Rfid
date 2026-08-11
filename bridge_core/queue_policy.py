from bridge_core.models import RawRFIDEvent


_ALLOWED_TRANSITIONS = {
    RawRFIDEvent.QueueState.UNASSIGNED: {
        RawRFIDEvent.QueueState.QUEUED,
        RawRFIDEvent.QueueState.REJECTED,
    },
    RawRFIDEvent.QueueState.QUEUED: {
        RawRFIDEvent.QueueState.INFLIGHT,
        RawRFIDEvent.QueueState.REJECTED,
        RawRFIDEvent.QueueState.DEAD,
    },
    RawRFIDEvent.QueueState.INFLIGHT: {
        RawRFIDEvent.QueueState.SENT,
        RawRFIDEvent.QueueState.RETRY,
        RawRFIDEvent.QueueState.REJECTED,
        RawRFIDEvent.QueueState.DEAD,
    },
    RawRFIDEvent.QueueState.RETRY: {
        RawRFIDEvent.QueueState.INFLIGHT,
        RawRFIDEvent.QueueState.DEAD,
    },
    RawRFIDEvent.QueueState.SENT: set(),
    RawRFIDEvent.QueueState.REJECTED: set(),
    RawRFIDEvent.QueueState.DEAD: set(),
}


def validate_queue_transition(*, current_state, target_state):
    valid_states = set(RawRFIDEvent.QueueState.values)

    if current_state not in valid_states:
        raise ValueError(f"Unknown current queue state: {current_state}")

    if target_state not in valid_states:
        raise ValueError(f"Unknown target queue state: {target_state}")

    if current_state == target_state:
        raise ValueError(
            f"Queue transition cannot remain in state: {current_state}"
        )

    if target_state not in _ALLOWED_TRANSITIONS[current_state]:
        raise ValueError(
            "Illegal queue transition: "
            f"{current_state} -> {target_state}"
        )


def calculate_retry_delay_seconds(
    *,
    attempt_number,
    initial_seconds,
    maximum_seconds,
):
    if attempt_number < 1:
        raise ValueError("Attempt number must be at least 1.")

    if initial_seconds < 1:
        raise ValueError("Initial retry seconds must be at least 1.")

    if maximum_seconds < initial_seconds:
        raise ValueError(
            "Maximum retry seconds cannot be less than initial seconds."
        )

    uncapped_delay = initial_seconds * (2 ** (attempt_number - 1))

    return min(uncapped_delay, maximum_seconds)
