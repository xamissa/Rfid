from dataclasses import dataclass

from bridge_core.models import RawRFIDEvent


@dataclass(frozen=True)
class IngestionResult:
    received_count: int
    created_count: int
    duplicate_count: int
    created_event_ids: tuple


def ingest_technical_reads(*, device, technical_reads):
    if not device.enabled:
        raise ValueError("Reader device must be enabled for ingestion.")

    reads = tuple(technical_reads)
    created_event_ids = []

    for technical_read in reads:
        event, created = RawRFIDEvent.objects.get_or_create(
            device=device,
            reader_event_key=technical_read.reader_event_key,
            defaults={
                "epc": technical_read.epc,
                "raw_payload": technical_read.raw_payload,
                "queue_state": RawRFIDEvent.QueueState.UNASSIGNED,
            },
        )

        if created:
            created_event_ids.append(event.event_id)

    created_event_ids = tuple(created_event_ids)

    return IngestionResult(
        received_count=len(reads),
        created_count=len(created_event_ids),
        duplicate_count=len(reads) - len(created_event_ids),
        created_event_ids=created_event_ids,
    )
