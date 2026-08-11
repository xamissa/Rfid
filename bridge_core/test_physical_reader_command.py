from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from bridge_core.management.commands.test_physical_rfid_reader import (
    CONFIRMATION_PHRASE,
    Command,
)
from bridge_core.models import ReaderDevice
from bridge_core.reader_backends import TechnicalRFIDRead


class PhysicalReaderTestCommandTests(SimpleTestCase):
    def make_device(self, **overrides):
        values = {
            "code": "receiving-door-01",
            "role": ReaderDevice.Role.RECEIVING,
            "host": "192.168.1.200",
            "port": 8090,
            "device_address": 1,
            "inventory_mode": ReaderDevice.InventoryMode.CACHED,
            "enabled": True,
        }
        values.update(overrides)

        return SimpleNamespace(**values)

    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "get_reader_backend"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "ReaderDevice.objects.get"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "settings"
    )
    def test_one_shot_scan_prints_reads_without_ingestion(
        self,
        mocked_settings,
        mocked_device_get,
        mocked_get_backend,
    ):
        mocked_settings.ALLOW_PHYSICAL_READER_CONTACT = True
        mocked_settings.ALLOW_ODOO_CONTACT = False
        mocked_settings.SENDER_BACKEND = "disabled"

        mocked_device_get.return_value = self.make_device()

        backend = Mock()
        backend.read_events.return_value = (
            TechnicalRFIDRead(
                reader_event_key="cached:event-1",
                epc="E2000017221101441890ABCD",
                raw_payload=(
                    "{\"source\":\"cached_inventory\","
                    "\"row_index\":0}"
                ),
            ),
        )
        mocked_get_backend.return_value = backend

        command = Command()
        command.stdout = StringIO()

        command.handle(
            device_code="receiving-door-01",
            scan_seconds=3.0,
            confirm_physical_contact=CONFIRMATION_PHRASE,
        )

        mocked_device_get.assert_called_once_with(
            code="receiving-door-01",
            enabled=True,
        )
        mocked_get_backend.assert_called_once_with(
            "cached_tcp",
            allow_physical_contact=True,
            scan_seconds=3.0,
        )
        backend.read_events.assert_called_once_with(
            device=mocked_device_get.return_value
        )

        output = command.stdout.getvalue()

        self.assertIn(
            "MODE=one-shot-physical-reader-test",
            output,
        )
        self.assertIn(
            "DATABASE_INGESTION=disabled",
            output,
        )
        self.assertIn(
            "ODOO_CONTACT=disabled",
            output,
        )
        self.assertIn(
            "TECHNICAL_READ_COUNT=1",
            output,
        )
        self.assertIn(
            "READ_1_EPC=E2000017221101441890ABCD",
            output,
        )
        self.assertIn(
            "PASS: One-shot physical RFID reader test completed",
            output,
        )

    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "get_reader_backend"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "settings"
    )
    def test_disabled_physical_contact_blocks_before_backend_selection(
        self,
        mocked_settings,
        mocked_get_backend,
    ):
        mocked_settings.ALLOW_PHYSICAL_READER_CONTACT = False
        mocked_settings.ALLOW_ODOO_CONTACT = False
        mocked_settings.SENDER_BACKEND = "disabled"

        command = Command()

        with self.assertRaisesMessage(
            CommandError,
            "disabled by configuration",
        ):
            command.handle(
                device_code="receiving-door-01",
                scan_seconds=3.0,
                confirm_physical_contact=CONFIRMATION_PHRASE,
            )

        mocked_get_backend.assert_not_called()

    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "get_reader_backend"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "settings"
    )
    def test_wrong_confirmation_blocks_before_backend_selection(
        self,
        mocked_settings,
        mocked_get_backend,
    ):
        mocked_settings.ALLOW_PHYSICAL_READER_CONTACT = True
        mocked_settings.ALLOW_ODOO_CONTACT = False
        mocked_settings.SENDER_BACKEND = "disabled"

        command = Command()

        with self.assertRaisesMessage(
            CommandError,
            "Exact physical-contact confirmation phrase required",
        ):
            command.handle(
                device_code="receiving-door-01",
                scan_seconds=3.0,
                confirm_physical_contact="WRONG",
            )

        mocked_get_backend.assert_not_called()

    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "get_reader_backend"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "settings"
    )
    def test_odoo_contact_enabled_blocks_test(
        self,
        mocked_settings,
        mocked_get_backend,
    ):
        mocked_settings.ALLOW_PHYSICAL_READER_CONTACT = True
        mocked_settings.ALLOW_ODOO_CONTACT = True
        mocked_settings.SENDER_BACKEND = "disabled"

        command = Command()

        with self.assertRaisesMessage(
            CommandError,
            "Odoo contact must remain disabled",
        ):
            command.handle(
                device_code="receiving-door-01",
                scan_seconds=3.0,
                confirm_physical_contact=CONFIRMATION_PHRASE,
            )

        mocked_get_backend.assert_not_called()

    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "get_reader_backend"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "settings"
    )
    def test_sender_enabled_blocks_test(
        self,
        mocked_settings,
        mocked_get_backend,
    ):
        mocked_settings.ALLOW_PHYSICAL_READER_CONTACT = True
        mocked_settings.ALLOW_ODOO_CONTACT = False
        mocked_settings.SENDER_BACKEND = "enabled"

        command = Command()

        with self.assertRaisesMessage(
            CommandError,
            "Sender backend must remain disabled",
        ):
            command.handle(
                device_code="receiving-door-01",
                scan_seconds=3.0,
                confirm_physical_contact=CONFIRMATION_PHRASE,
            )

        mocked_get_backend.assert_not_called()

    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "get_reader_backend"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "ReaderDevice.objects.get"
    )
    @patch(
        "bridge_core.management.commands.test_physical_rfid_reader."
        "settings"
    )
    def test_active_mode_device_is_rejected_before_contact(
        self,
        mocked_settings,
        mocked_device_get,
        mocked_get_backend,
    ):
        mocked_settings.ALLOW_PHYSICAL_READER_CONTACT = True
        mocked_settings.ALLOW_ODOO_CONTACT = False
        mocked_settings.SENDER_BACKEND = "disabled"
        mocked_device_get.return_value = self.make_device(
            inventory_mode=ReaderDevice.InventoryMode.ACTIVE
        )

        command = Command()

        with self.assertRaisesMessage(
            CommandError,
            "cached inventory mode only",
        ):
            command.handle(
                device_code="receiving-door-01",
                scan_seconds=3.0,
                confirm_physical_contact=CONFIRMATION_PHRASE,
            )

        mocked_get_backend.assert_not_called()
