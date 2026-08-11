from dataclasses import dataclass


@dataclass(frozen=True)
class TechnicalRFIDRead:
    reader_event_key: str
    epc: str
    raw_payload: str


class FakeReaderBackend:
    """Offline reader backend that emits only explicitly supplied test reads."""

    def __init__(self, reads=()):
        self._reads = tuple(reads)

    def read_events(self, *, device):
        del device
        return self._reads


def get_reader_backend(
    backend_name,
    *,
    allow_physical_contact=False,
    scan_seconds=3.0,
):
    normalized_name = backend_name.strip().lower()

    if normalized_name == "fake":
        return FakeReaderBackend()

    if normalized_name == "cached_tcp":
        if not allow_physical_contact:
            raise ValueError(
                "Physical reader contact is not allowed."
            )

        from bridge_core.physical_reader_backend import (
            CachedTCPPhysicalReaderBackend,
        )

        return CachedTCPPhysicalReaderBackend(
            scan_seconds=scan_seconds,
        )

    if normalized_name == "active_tcp":
        if not allow_physical_contact:
            raise ValueError(
                "Physical reader contact is not allowed."
            )

        from bridge_core.physical_reader_backend import (
            ActiveTCPPhysicalReaderBackend,
        )

        return ActiveTCPPhysicalReaderBackend(
            scan_seconds=scan_seconds,
        )

    raise ValueError(f"Unsupported reader backend: {backend_name}")
