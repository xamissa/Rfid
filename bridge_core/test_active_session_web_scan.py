from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from bridge_core.models import (
    OperationalConfiguration,
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)


@override_settings(
    ALLOW_PHYSICAL_READER_CONTACT=False,
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class ActiveSessionWebScanTests(TestCase):
    def setUp(self):
        self.configuration = OperationalConfiguration.objects.get(
            name="default"
        )
        self.configuration.setup_completed = True
        self.configuration.poc_reader_backend = (
            OperationalConfiguration
            .PocReaderBackend
            .ACTIVE_TCP
        )
        self.configuration.poc_allow_physical_reader_contact = True
        self.configuration.poc_allow_odoo_contact = False
        self.configuration.odoo_integration_enabled = False
        self.configuration.save()

        self.reader = ReaderDevice.objects.create(
            code="door1",
            name="Door1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            inventory_mode=ReaderDevice.InventoryMode.CACHED,
            enabled=True,
        )

        self.session = RFIDSession.objects.create(
            external_session_key="web-active-session-001",
            device=self.reader,
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_model="stock.picking",
            odoo_record_id=1001,
            odoo_reference="WH/IN/WEB-001",
            status=RFIDSession.Status.ACTIVE,
        )

        self.user = get_user_model().objects.create_user(
            username="rfid-web-staff",
            password="safe-test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)

        self.url = reverse(
            "bridge_core:active_session_scan",
            args=(self.session.pk,),
        )

    def test_get_is_not_allowed(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    @patch("bridge_core.views.call_command")
    def test_valid_post_invokes_certified_command(
        self,
        mocked_call_command,
    ):
        response = self.client.post(
            self.url,
            {
                "scan_seconds": "10",
                "confirmation": (
                    "SCAN_AND_STORE_ACTIVE_SESSION"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)

        mocked_call_command.assert_called_once_with(
            "run_active_rfid_session_scan",
            device_code="door1",
            scan_seconds=10.0,
            confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
            apply=True,
        )

    @patch("bridge_core.views.call_command")
    def test_wrong_confirmation_blocks_before_command(
        self,
        mocked_call_command,
    ):
        response = self.client.post(
            self.url,
            {
                "scan_seconds": "10",
                "confirmation": "WRONG",
            },
        )

        self.assertEqual(response.status_code, 302)
        mocked_call_command.assert_not_called()

    @patch("bridge_core.views.call_command")
    def test_non_active_session_blocks_before_command(
        self,
        mocked_call_command,
    ):
        self.session.status = RFIDSession.Status.CLOSED
        self.session.save()

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "10",
                "confirmation": (
                    "SCAN_AND_STORE_ACTIVE_SESSION"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        mocked_call_command.assert_not_called()

    @patch("bridge_core.views.call_command")
    def test_non_active_backend_blocks_before_command(
        self,
        mocked_call_command,
    ):
        self.configuration.poc_reader_backend = (
            OperationalConfiguration
            .PocReaderBackend
            .CACHED_TCP
        )
        self.configuration.save()

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "10",
                "confirmation": (
                    "SCAN_AND_STORE_ACTIVE_SESSION"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        mocked_call_command.assert_not_called()

    @patch("bridge_core.views.call_command")
    def test_odoo_contact_blocks_before_command(
        self,
        mocked_call_command,
    ):
        self.configuration.poc_allow_odoo_contact = True
        self.configuration.save()

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "10",
                "confirmation": (
                    "SCAN_AND_STORE_ACTIVE_SESSION"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        mocked_call_command.assert_not_called()

    @patch("bridge_core.views.call_command")
    def test_command_error_is_handled_safely(
        self,
        mocked_call_command,
    ):
        mocked_call_command.side_effect = CommandError(
            "controlled scan failure"
        )

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "10",
                "confirmation": (
                    "SCAN_AND_STORE_ACTIVE_SESSION"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(RawRFIDEvent.objects.count(), 0)
