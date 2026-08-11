from dataclasses import dataclass

from bridge_core.models import RFIDSession
from bridge_core.session_assignment import (
    assign_event_to_active_session,
)


@dataclass(frozen=True)
class AssignmentCycleResult:
    selected_count: int
    assigned_count: int
    unassigned_count: int
    failed_count: int


def run_active_session_assignment_cycle(
    *,
    event_ids,
    assignment_function=assign_event_to_active_session,
):
    selected_event_ids = tuple(event_ids)
    assigned_count = 0
    unassigned_count = 0
    failed_count = 0

    for event_id in selected_event_ids:
        try:
            assignment_function(event_id=event_id)
        except RFIDSession.DoesNotExist:
            unassigned_count += 1
            continue
        except Exception:
            failed_count += 1
            continue

        assigned_count += 1

    return AssignmentCycleResult(
        selected_count=len(selected_event_ids),
        assigned_count=assigned_count,
        unassigned_count=unassigned_count,
        failed_count=failed_count,
    )
