from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from bridge_core.models import RawRFIDEvent, ReaderDevice
from bridge_core.reader_backends import TechnicalRFIDRead


class ReaderWebHardwareTestTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.staff_user = user_model.objects.create_user(
            username="staff-operator",
            password="safe-test-password",
            is_staff=True,
        )
        self.normal_user = user_model.objects.create_user(
            username="normal-operator",
            password="safe-test-password",
            is_staff=False,
        )
        self.reader = ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving door reader",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.200",
            port=8090,
            device_address=1,
            inventory_mode=ReaderDevice.InventoryMode.CACHED,
            connect_timeout_seconds=5,
            read_timeout_seconds=5,
            reconnect_delay_seconds=5,
            enabled=True,
        )
        self.url = reverse(
            "bridge_core:reader_validation",
            args=(self.reader.pk,),
        )

    @patch("bridge_core.views.get_reader_backend")
    def test_staff_get_displays_confirmation_without_contact(
        self,
        mocked_get_backend,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "CONTACT_THIS_READER_ONCE",
        )
        mocked_get_backend.assert_not_called()

    def test_nonstaff_user_cannot_access_test_page(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=False,
        ALLOW_ODOO_CONTACT=False,
        SENDER_BACKEND="disabled",
    )
    @patch("bridge_core.views.get_reader_backend")
    def test_blocked_global_flag_prevents_contact(
        self,
        mocked_get_backend,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "3",
                "confirmation": (
                    "CONTACT_THIS_READER_ONCE"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Physical reader contact is disabled",
        )
        mocked_get_backend.assert_not_called()

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=False,
        SENDER_BACKEND="disabled",
    )
    @patch("bridge_core.views.get_reader_backend")
    def test_staff_post_runs_one_scan_without_ingestion(
        self,
        mocked_get_backend,
    ):
        backend = Mock()
        backend.read_events.return_value = (
            TechnicalRFIDRead(
                reader_event_key="cached:event-1",
                epc="E2000017221101441890ABCD",
                raw_payload=(
                    "{\"source\":\"cached_inventory\"}"
                ),
            ),
        )
        mocked_get_backend.return_value = backend

        self.client.force_login(self.staff_user)

        before_count = RawRFIDEvent.objects.count()

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "4",
                "confirmation": (
                    "CONTACT_THIS_READER_ONCE"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "E2000017221101441890ABCD",
        )
        self.assertEqual(
            RawRFIDEvent.objects.count(),
            before_count,
        )
        mocked_get_backend.assert_called_once_with(
            "cached_tcp",
            allow_physical_contact=True,
            scan_seconds=4.0,
        )
        backend.read_events.assert_called_once_with(
            device=self.reader,
        )

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=False,
        SENDER_BACKEND="disabled",
    )
    @patch("bridge_core.views.get_reader_backend")
    def test_wrong_confirmation_prevents_contact(
        self,
        mocked_get_backend,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "3",
                "confirmation": "WRONG",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "exact physical-contact confirmation",
        )
        mocked_get_backend.assert_not_called()

    @override_settings(
        ALLOW_PHYSICAL_READER_CONTACT=True,
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch("bridge_core.views.get_reader_backend")
    def test_odoo_contact_enabled_prevents_reader_validation(
        self,
        mocked_get_backend,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {
                "scan_seconds": "3",
                "confirmation": (
                    "CONTACT_THIS_READER_ONCE"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Odoo contact must remain disabled",
        )
        mocked_get_backend.assert_not_called()
