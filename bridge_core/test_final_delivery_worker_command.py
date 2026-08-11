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
