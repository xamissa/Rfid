from dataclasses import dataclass

from bridge_core.ingestion import ingest_technical_reads
from bridge_core.session_assignment_cycle import (
    run_active_session_assignment_cycle,
)


@dataclass(frozen=True)
class WorkerCycleResult:
    device_count: int
    received_count: int
    created_count: int
    duplicate_count: int
    assignment_selected_count: int
    assigned_count: int
    unassigned_count: int
    assignment_failed_count: int


def run_device_ingestion_cycle(
    *,
    devices,
    reader_backend,
    ingestion_function=ingest_technical_reads,
    assignment_function=run_active_session_assignment_cycle,
):
    device_count = 0
    received_count = 0
    created_count = 0
    duplicate_count = 0
    assignment_selected_count = 0
    assigned_count = 0
    unassigned_count = 0
    assignment_failed_count = 0

    for device in devices:
        if not device.enabled:
            raise ValueError(
                "Worker cycle received a disabled reader device."
            )

        technical_reads = reader_backend.read_events(device=device)
        ingestion_result = ingestion_function(
            device=device,
            technical_reads=technical_reads,
        )
        assignment_result = assignment_function(
            event_ids=ingestion_result.created_event_ids,
        )

        device_count += 1
        received_count += ingestion_result.received_count
        created_count += ingestion_result.created_count
        duplicate_count += ingestion_result.duplicate_count
        assignment_selected_count += assignment_result.selected_count
        assigned_count += assignment_result.assigned_count
        unassigned_count += assignment_result.unassigned_count
        assignment_failed_count += assignment_result.failed_count

    return WorkerCycleResult(
        device_count=device_count,
        received_count=received_count,
        created_count=created_count,
        duplicate_count=duplicate_count,
        assignment_selected_count=assignment_selected_count,
        assigned_count=assigned_count,
        unassigned_count=unassigned_count,
        assignment_failed_count=assignment_failed_count,
    )
