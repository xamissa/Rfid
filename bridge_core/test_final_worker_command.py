from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from bridge_core.models import ReaderDevice, RFIDSession
from bridge_core.odoo_api_v1 import (
    OdooRFIDApiProtocolError,
    OdooRFIDApiTransportError,
)


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

class FinalWorkerCommandTransportRetryTests(TestCase):
    def _reader(self):
        return ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            inventory_mode=ReaderDevice.InventoryMode.ACTIVE,
            enabled=True,
        )

    @staticmethod
    def _configuration():
        return SimpleNamespace(
            odoo_base_url="https://example.invalid",
            bearer_token="a" * 40,
            gateway_code="RFID-GW-01",
            reader_code="receiving-door-01",
            request_timeout_seconds=10,
            verify_tls=True,
            poll_seconds=1.0,
        )

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.time.sleep"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.FinalWorkerCycle"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.PersistentActiveReaderExecutor"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.load_final_runtime_configuration"
    )
    def test_continuous_worker_retries_transport_error(
        self,
        mocked_load_configuration,
        mocked_executor_class,
        mocked_worker_class,
        mocked_sleep,
    ):
        self._reader()
        mocked_load_configuration.return_value = (
            self._configuration()
        )

        executor = Mock()
        executor.is_active = False
        mocked_executor_class.return_value = executor

        worker = Mock()
        worker.run_once.side_effect = [
            OdooRFIDApiTransportError(
                "temporary name resolution failure"
            ),
            KeyboardInterrupt(),
        ]
        mocked_worker_class.return_value = worker

        stdout = StringIO()

        call_command(
            "run_final_rfid_worker",
            stdout=stdout,
        )

        self.assertEqual(
            worker.run_once.call_count,
            2,
        )
        mocked_sleep.assert_called_once_with(1.0)

        self.assertIn(
            "RETRY: transient Odoo RFID API transport failure",
            stdout.getvalue(),
        )
        self.assertIn(
            "HOLD: final RFID worker shutdown requested",
            stdout.getvalue(),
        )

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.FinalWorkerCycle"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.PersistentActiveReaderExecutor"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.load_final_runtime_configuration"
    )
    def test_protocol_error_still_fails_closed(
        self,
        mocked_load_configuration,
        mocked_executor_class,
        mocked_worker_class,
    ):
        self._reader()
        mocked_load_configuration.return_value = (
            self._configuration()
        )

        executor = Mock()
        executor.is_active = False
        mocked_executor_class.return_value = executor

        worker = Mock()
        worker.run_once.side_effect = (
            OdooRFIDApiProtocolError(
                "invalid Odoo response"
            )
        )
        mocked_worker_class.return_value = worker

        with self.assertRaises(
            OdooRFIDApiProtocolError
        ):
            call_command(
                "run_final_rfid_worker",
            )

        self.assertEqual(
            worker.run_once.call_count,
            1,
        )

class FinalWorkerReaderOverrideTests(TestCase):
    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.FinalWorkerCycle"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.PersistentActiveReaderExecutor"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_worker.load_final_runtime_configuration"
    )
    def test_reader_code_override_selects_dispatch_reader(
        self,
        mocked_load_configuration,
        mocked_executor_class,
        mocked_worker_class,
    ):
        ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            inventory_mode=ReaderDevice.InventoryMode.ACTIVE,
            enabled=True,
        )

        dispatch = ReaderDevice.objects.create(
            code="dispatch-door-01",
            name="Dispatch Door 1",
            role=ReaderDevice.Role.DISPATCH,
            host="192.0.2.20",
            port=8090,
            device_address=2,
            inventory_mode=ReaderDevice.InventoryMode.ACTIVE,
            enabled=True,
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
        executor.is_active = False
        mocked_executor_class.return_value = executor

        worker = Mock()
        worker.run_once.return_value = SimpleNamespace(
            runtime_state="idle",
            commands_processed=0,
            tag_frames_received=0,
            tags_created=0,
        )
        mocked_worker_class.return_value = worker

        stdout = StringIO()

        call_command(
            "run_final_rfid_worker",
            reader_code="dispatch-door-01",
            once=True,
            stdout=stdout,
        )

        mocked_executor_class.assert_called_once_with(
            device=dispatch,
        )

        executor.verify_idle.assert_called_once_with()

        self.assertIn(
            "RFID_READER_CODE=dispatch-door-01",
            stdout.getvalue(),
        )

        self.assertNotIn(
            "RFID_READER_CODE=receiving-door-01",
            stdout.getvalue(),
        )
