from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings


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
