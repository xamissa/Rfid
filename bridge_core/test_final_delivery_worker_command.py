from unittest.mock import patch
from types import SimpleNamespace
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import (
    TestCase,
    override_settings,
)


class FinalDeliveryWorkerSafetyTests(TestCase):
    @override_settings(
        ALLOW_ODOO_CONTACT=False,
        SENDER_BACKEND="disabled",
    )
    def test_command_refuses_current_safe_configuration(self):
        with self.assertRaisesMessage(
            CommandError,
            "ALLOW_ODOO_CONTACT=True",
        ):
            call_command(
                "run_final_rfid_delivery_worker",
                once=True,
            )

    @override_settings(
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="odoo",
    )
    def test_legacy_sender_must_remain_disabled(self):
        with self.assertRaisesMessage(
            CommandError,
            "SENDER_BACKEND=disabled",
        ):
            call_command(
                "run_final_rfid_delivery_worker",
                once=True,
            )

    @override_settings(
        ALLOW_ODOO_CONTACT=False,
        ALLOW_PHYSICAL_READER_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    def test_reader_gate_does_not_enable_delivery(self):
        with self.assertRaisesMessage(
            CommandError,
            "ALLOW_ODOO_CONTACT=True",
        ):
            call_command(
                "run_final_rfid_delivery_worker",
                once=True,
            )

class FinalDeliveryWorkerReaderOverrideTests(TestCase):
    @override_settings(
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_delivery_worker.run_final_delivery_cycle"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_delivery_worker.FinalOdooEventSender"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_delivery_worker.OdooRFIDApiClient"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_delivery_worker."
        "OperationalConfiguration.objects.get"
    )
    @patch(
        "bridge_core.management.commands."
        "run_final_rfid_delivery_worker."
        "load_final_runtime_configuration"
    )
    def test_reader_code_override_scopes_dispatch_delivery(
        self,
        mocked_load_configuration,
        mocked_operational_get,
        mocked_api_client_class,
        mocked_sender_class,
        mocked_delivery_cycle,
    ):
        mocked_load_configuration.return_value = SimpleNamespace(
            odoo_base_url="https://example.invalid",
            bearer_token="a" * 40,
            gateway_code="RFID-GW-01",
            reader_code="receiving-door-01",
            request_timeout_seconds=10,
            verify_tls=True,
        )

        mocked_operational_get.return_value = SimpleNamespace(
            worker_batch_size=100,
            max_delivery_attempts=5,
            retry_initial_seconds=1,
            retry_max_seconds=60,
        )

        sender = mocked_sender_class.return_value

        mocked_delivery_cycle.return_value = SimpleNamespace(
            selected_count=0,
            processed_count=0,
            sent_count=0,
            retry_count=0,
            rejected_count=0,
            dead_count=0,
            exhausted_dead_count=0,
            failed_count=0,
        )

        stdout = StringIO()

        call_command(
            "run_final_rfid_delivery_worker",
            reader_code="dispatch-door-01",
            once=True,
            stdout=stdout,
        )

        mocked_sender_class.assert_called_once_with(
            api_client=mocked_api_client_class.return_value,
            reader_code="dispatch-door-01",
        )

        kwargs = mocked_delivery_cycle.call_args.kwargs

        self.assertIs(
            kwargs["sender"],
            sender,
        )

        self.assertEqual(
            kwargs["reader_code"],
            "dispatch-door-01",
        )

        self.assertIn(
            "RFID_READER_CODE=dispatch-door-01",
            stdout.getvalue(),
        )

        self.assertNotIn(
            "RFID_READER_CODE=receiving-door-01",
            stdout.getvalue(),
        )
