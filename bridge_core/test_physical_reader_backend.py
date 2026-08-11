from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from bridge_core.models import ReaderDevice
from bridge_core.physical_reader_backend import (
    CachedTCPPhysicalReaderBackend,
    PhysicalReaderBackendError,
)
from bridge_core.reader_backends import TechnicalRFIDRead
from bridge_core.reader_transport import TCPReaderConnection


class CachedTCPPhysicalReaderBackendTests(SimpleTestCase):
    def make_device(self, **overrides):
        values = {
            "code": "receiving-door-01",
            "enabled": True,
            "inventory_mode": ReaderDevice.InventoryMode.CACHED,
            "host": " 192.168.1.200 ",
            "port": 8090,
            "device_address": 1,
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 7,
        }
        values.update(overrides)

        return SimpleNamespace(**values)

    def test_runs_complete_cached_scan_through_one_session(self):
        session = Mock()
        session_context = Mock()
        session_context.__enter__ = Mock(
            return_value=session
        )
        session_context.__exit__ = Mock(
            return_value=None
        )

        transport = Mock()
        transport.open_session.return_value = (
            session_context
        )
        transport_factory = Mock(
            return_value=transport
        )

        inventory_result = object()
        client = Mock()
        client.run.return_value = inventory_result
        client_factory = Mock(return_value=client)

        technical_reads = (
            TechnicalRFIDRead(
                reader_event_key="event-1",
                epc="E2000017221101441890ABCD",
                raw_payload="{}",
            ),
        )
        result_adapter = Mock(
            return_value=technical_reads
        )

        backend = CachedTCPPhysicalReaderBackend(
            scan_seconds=3.0,
            transport_factory=transport_factory,
            client_factory=client_factory,
            result_adapter=result_adapter,
            scan_token_factory=Mock(
                return_value="scan-token-001"
            ),
        )
        device = self.make_device()

        result = backend.read_events(device=device)

        transport_factory.assert_called_once_with(
            connection=TCPReaderConnection(
                host="192.168.1.200",
                port=8090,
                connect_timeout_seconds=5,
                read_timeout_seconds=7,
            )
        )
        client_factory.assert_called_once_with(
            address=1,
            scan_seconds=3.0,
        )
        transport.open_session.assert_called_once_with()
        client.run.assert_called_once_with(
            session=session
        )
        session_context.__exit__.assert_called_once()

        result_adapter.assert_called_once_with(
            result=inventory_result,
            scan_id=(
                "receiving-door-01:"
                "scan-token-001"
            ),
        )
        self.assertEqual(result, technical_reads)

    def test_disabled_device_is_rejected_before_contact(self):
        transport_factory = Mock()
        client_factory = Mock()

        backend = CachedTCPPhysicalReaderBackend(
            transport_factory=transport_factory,
            client_factory=client_factory,
        )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "must be enabled",
        ):
            backend.read_events(
                device=self.make_device(enabled=False)
            )

        transport_factory.assert_not_called()
        client_factory.assert_not_called()

    def test_active_inventory_mode_is_rejected_before_contact(self):
        transport_factory = Mock()

        backend = CachedTCPPhysicalReaderBackend(
            transport_factory=transport_factory,
        )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "cached inventory mode only",
        ):
            backend.read_events(
                device=self.make_device(
                    inventory_mode=(
                        ReaderDevice.InventoryMode.ACTIVE
                    )
                )
            )

        transport_factory.assert_not_called()

    def test_empty_host_is_rejected_before_contact(self):
        transport_factory = Mock()

        backend = CachedTCPPhysicalReaderBackend(
            transport_factory=transport_factory,
        )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "host cannot be empty",
        ):
            backend.read_events(
                device=self.make_device(host="   ")
            )

        transport_factory.assert_not_called()

    def test_empty_generated_scan_token_is_rejected(self):
        transport_factory = Mock()

        backend = CachedTCPPhysicalReaderBackend(
            transport_factory=transport_factory,
            scan_token_factory=Mock(
                return_value="   "
            ),
        )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "scan token cannot be empty",
        ):
            backend.read_events(
                device=self.make_device()
            )

        transport_factory.assert_not_called()

    def test_invalid_scan_duration_is_rejected(self):
        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "duration must be between",
        ):
            CachedTCPPhysicalReaderBackend(
                scan_seconds=301
            )


class ActiveTCPPhysicalReaderBackendTests(SimpleTestCase):
    def make_device(self, **overrides):
        values = {
            "code": "door1",
            "enabled": True,
            "inventory_mode": ReaderDevice.InventoryMode.ACTIVE,
            "host": " 192.168.1.201 ",
            "port": 8090,
            "device_address": 2,
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 5,
        }
        values.update(overrides)

        return SimpleNamespace(**values)

    def test_runs_active_scan_through_one_tcp_session(self):
        from bridge_core.physical_reader_backend import (
            ActiveTCPPhysicalReaderBackend,
        )

        session = Mock()
        session_context = Mock()
        session_context.__enter__ = Mock(
            return_value=session
        )
        session_context.__exit__ = Mock(
            return_value=None
        )

        transport = Mock()
        transport.open_session.return_value = session_context
        transport_factory = Mock(return_value=transport)

        inventory_result = object()
        client = Mock()
        client.run.return_value = inventory_result
        client_factory = Mock(return_value=client)

        technical_reads = (
            TechnicalRFIDRead(
                reader_event_key="active-event-1",
                epc="E2801191A50300631AB2F621",
                raw_payload="{}",
            ),
        )
        result_adapter = Mock(
            return_value=technical_reads
        )

        backend = ActiveTCPPhysicalReaderBackend(
            scan_seconds=10.0,
            transport_factory=transport_factory,
            client_factory=client_factory,
            result_adapter=result_adapter,
            scan_token_factory=Mock(
                return_value="active-token-001"
            ),
        )

        result = backend.read_events(
            device=self.make_device()
        )

        transport_factory.assert_called_once_with(
            connection=TCPReaderConnection(
                host="192.168.1.201",
                port=8090,
                connect_timeout_seconds=5,
                read_timeout_seconds=5,
            )
        )
        client_factory.assert_called_once_with(
            address=2,
            scan_seconds=10.0,
        )
        client.run.assert_called_once_with(
            session=session
        )
        result_adapter.assert_called_once_with(
            result=inventory_result,
            scan_id="door1:active-token-001",
        )
        self.assertEqual(result, technical_reads)

    def test_cached_mode_is_rejected_before_contact(self):
        from bridge_core.physical_reader_backend import (
            ActiveTCPPhysicalReaderBackend,
        )

        transport_factory = Mock()
        backend = ActiveTCPPhysicalReaderBackend(
            transport_factory=transport_factory,
        )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "requires active reporting mode",
        ):
            backend.read_events(
                device=self.make_device(
                    inventory_mode=ReaderDevice.InventoryMode.CACHED
                )
            )

        transport_factory.assert_not_called()

    def test_disabled_active_device_is_rejected_before_contact(self):
        from bridge_core.physical_reader_backend import (
            ActiveTCPPhysicalReaderBackend,
        )

        transport_factory = Mock()
        backend = ActiveTCPPhysicalReaderBackend(
            transport_factory=transport_factory,
        )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "must be enabled",
        ):
            backend.read_events(
                device=self.make_device(enabled=False)
            )

        transport_factory.assert_not_called()

    def test_invalid_active_scan_duration_is_rejected(self):
        from bridge_core.physical_reader_backend import (
            ActiveTCPPhysicalReaderBackend,
        )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "greater than 0",
        ):
            ActiveTCPPhysicalReaderBackend(
                scan_seconds=0
            )

        with self.assertRaisesMessage(
            PhysicalReaderBackendError,
            "no more than 600",
        ):
            ActiveTCPPhysicalReaderBackend(
                scan_seconds=601
            )
