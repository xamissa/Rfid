from uuid import uuid4

from bridge_core.models import ReaderDevice
from bridge_core.reader_client import (
    ActiveInventoryReaderClient,
    CachedInventoryReaderClient,
)
from bridge_core.reader_result_adapter import (
    active_inventory_to_technical_reads,
    cached_inventory_to_technical_reads,
)
from bridge_core.reader_transport import (
    TCPReaderConnection,
    TCPReaderTransport,
)


class PhysicalReaderBackendError(RuntimeError):
    pass


def generate_scan_token() -> str:
    return uuid4().hex


class CachedTCPPhysicalReaderBackend:
    def __init__(
        self,
        *,
        scan_seconds: float = 3.0,
        transport_factory=TCPReaderTransport,
        client_factory=CachedInventoryReaderClient,
        result_adapter=cached_inventory_to_technical_reads,
        scan_token_factory=generate_scan_token,
    ):
        if not 0 <= scan_seconds <= 300:
            raise PhysicalReaderBackendError(
                "Scan duration must be between 0 and 300 seconds."
            )

        self._scan_seconds = scan_seconds
        self._transport_factory = transport_factory
        self._client_factory = client_factory
        self._result_adapter = result_adapter
        self._scan_token_factory = scan_token_factory

    def read_events(self, *, device):
        if not device.enabled:
            raise PhysicalReaderBackendError(
                "Physical reader device must be enabled."
            )

        if device.inventory_mode != ReaderDevice.InventoryMode.CACHED:
            raise PhysicalReaderBackendError(
                "Physical reader backend currently supports "
                "cached inventory mode only."
            )

        host = device.host.strip()

        if not host:
            raise PhysicalReaderBackendError(
                "Physical reader host cannot be empty."
            )

        scan_token = str(
            self._scan_token_factory()
        ).strip()

        if not scan_token:
            raise PhysicalReaderBackendError(
                "Generated scan token cannot be empty."
            )

        scan_id = f"{device.code}:{scan_token}"

        connection = TCPReaderConnection(
            host=host,
            port=device.port,
            connect_timeout_seconds=(
                device.connect_timeout_seconds
            ),
            read_timeout_seconds=(
                device.read_timeout_seconds
            ),
        )
        transport = self._transport_factory(
            connection=connection
        )
        client = self._client_factory(
            address=device.device_address,
            scan_seconds=self._scan_seconds,
        )

        with transport.open_session() as session:
            result = client.run(session=session)

        return self._result_adapter(
            result=result,
            scan_id=scan_id,
        )


class ActiveTCPPhysicalReaderBackend:
    def __init__(
        self,
        *,
        scan_seconds: float = 3.0,
        transport_factory=TCPReaderTransport,
        client_factory=ActiveInventoryReaderClient,
        result_adapter=active_inventory_to_technical_reads,
        scan_token_factory=generate_scan_token,
    ):
        if not 0 < scan_seconds <= 600:
            raise PhysicalReaderBackendError(
                "Active scan duration must be greater than 0 "
                "and no more than 600 seconds."
            )

        self._scan_seconds = scan_seconds
        self._transport_factory = transport_factory
        self._client_factory = client_factory
        self._result_adapter = result_adapter
        self._scan_token_factory = scan_token_factory

    def read_events(self, *, device):
        if not device.enabled:
            raise PhysicalReaderBackendError(
                "Physical reader device must be enabled."
            )

        if device.inventory_mode != ReaderDevice.InventoryMode.ACTIVE:
            raise PhysicalReaderBackendError(
                "Active physical reader backend requires "
                "active reporting mode."
            )

        host = device.host.strip()

        if not host:
            raise PhysicalReaderBackendError(
                "Physical reader host cannot be empty."
            )

        scan_token = str(
            self._scan_token_factory()
        ).strip()

        if not scan_token:
            raise PhysicalReaderBackendError(
                "Generated scan token cannot be empty."
            )

        scan_id = f"{device.code}:{scan_token}"

        connection = TCPReaderConnection(
            host=host,
            port=device.port,
            connect_timeout_seconds=(
                device.connect_timeout_seconds
            ),
            read_timeout_seconds=(
                device.read_timeout_seconds
            ),
        )
        transport = self._transport_factory(
            connection=connection
        )
        client = self._client_factory(
            address=device.device_address,
            scan_seconds=self._scan_seconds,
        )

        with transport.open_session() as session:
            result = client.run(session=session)

        return self._result_adapter(
            result=result,
            scan_id=scan_id,
        )

