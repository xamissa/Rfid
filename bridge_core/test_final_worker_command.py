from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from bridge_core.models import ReaderDevice, RFIDSession


class FinalWorkerCommandSafetyTests(TestCase):
    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=False,
        ALLOW_ODOO_CONTACT=False,
        SENDER_BACKEND="disabled",
    )
    def test_command_refuses_current_safe_configuration(self):
        with self.assertRaisesMessage(
            CommandError,
            "ALLOW_PHYSICAL_READER_CONTACT=True",
        ):
            call_command(
                "run_final_rfid_worker",
                once=True,
            )

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=False,
        SENDER_BACKEND="disabled",
    )
    def test_command_requires_odoo_gate_too(self):
        with self.assertRaisesMessage(
            CommandError,
            "ALLOW_ODOO_CONTACT=True",
        ):
            call_command(
                "run_final_rfid_worker",
                once=True,
            )

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker."
        "OdooRFIDApiClient"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker."
        "PersistentActiveReaderExecutor"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker."
        "load_final_runtime_configuration"
    )
    def test_stale_session_is_forced_idle_before_startup_refusal(
        self,
        mocked_load_configuration,
        mocked_executor_class,
        mocked_api_client_class,
    ):
        reader = ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            inventory_mode=ReaderDevice.InventoryMode.ACTIVE,
            enabled=True,
        )

        stale_session = RFIDSession.objects.create(
            external_session_key="reboot-stale-session",
            device=reader,
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_model="stock.picking",
            odoo_record_id=0,
            odoo_reference="EXWS1/IN/02227",
            status=RFIDSession.Status.ACTIVE,
        )

        mocked_load_configuration.return_value = SimpleNamespace(
            odoo_base_url="https://example.invalid",
            bearer_token="a" * 40,
            gateway_code="RFID-GW-01",
            reader_code="receiving-door-01",
            request_timeout_seconds=10,
            verify_tls=True,
            poll_seconds=1.0,
        )

        executor = Mock()
        mocked_executor_class.return_value = executor

        with self.assertRaisesMessage(
            CommandError,
            "forced to a verified idle state",
        ):
            call_command(
                "run_final_rfid_worker",
                once=True,
            )

        executor.verify_idle.assert_called_once_with()

        stale_session.refresh_from_db()

        self.assertEqual(
            stale_session.status,
            RFIDSession.Status.ACTIVE,
        )

        self.assertIsNone(
            stale_session.closed_at,
        )

        mocked_api_client_class.return_value.heartbeat.assert_not_called()
        mocked_api_client_class.return_value.commands.assert_not_called()
        mocked_api_client_class.return_value.ack.assert_not_called()
