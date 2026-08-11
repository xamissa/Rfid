from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from bridge_core.forms import (
    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE,
)
from bridge_core.models import (
    OperationalConfiguration,
    ReaderDevice,
)


@override_settings(
    ALLOW_PHYSICAL_READER_CONTACT=False,
    ALLOW_ODOO_CONTACT=False,
    READER_BACKEND="fake",
    SENDER_BACKEND="disabled",
)
class PocControlCentreWebTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.staff_user = user_model.objects.create_user(
            username="poc-control-staff",
            password="safe-test-password",
            is_staff=True,
        )
        self.normal_user = user_model.objects.create_user(
            username="poc-control-normal",
            password="safe-test-password",
            is_staff=False,
        )
        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )
        self.url = reverse(
            "bridge_core:poc_control_centre"
        )

    def test_anonymous_user_is_redirected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_nonstaff_user_is_redirected(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_staff_page_shows_fail_closed_controls(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "POC control centre",
        )
        self.assertContains(
            response,
            "1/13",
        )
        self.assertContains(
            response,
            "Worker and environment remain unchanged",
        )
        self.assertContains(
            response,
            POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE,
        )

    @patch("bridge_core.views.get_reader_backend")
    @patch(
        "bridge_core.views.execute_odoo_connection_test"
    )
    @patch(
        "bridge_core.views.execute_inventory_count_poc"
    )
    def test_save_controls_makes_no_external_contact(
        self,
        mocked_inventory,
        mocked_connection,
        mocked_reader_backend,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {
                "poc_reader_backend": "cached_tcp",
                "poc_allow_physical_reader_contact": "on",
                "poc_allow_odoo_contact": "on",
                "confirmation": (
                    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
                ),
            },
        )

        self.assertEqual(response.status_code, 302)

        self.configuration.refresh_from_db()

        self.assertEqual(
            self.configuration.poc_reader_backend,
            "cached_tcp",
        )
        self.assertTrue(
            self.configuration
            .poc_allow_physical_reader_contact
        )
        self.assertTrue(
            self.configuration.poc_allow_odoo_contact
        )

        mocked_reader_backend.assert_not_called()
        mocked_connection.assert_not_called()
        mocked_inventory.assert_not_called()

    def test_invalid_confirmation_keeps_contact_blocked(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {
                "poc_reader_backend": "cached_tcp",
                "poc_allow_physical_reader_contact": "on",
                "poc_allow_odoo_contact": "on",
                "confirmation": "wrong",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Enter the exact confirmation phrase",
        )

        self.configuration.refresh_from_db()

        self.assertFalse(
            self.configuration
            .poc_allow_physical_reader_contact
        )
        self.assertFalse(
            self.configuration.poc_allow_odoo_contact
        )

    def test_disabling_controls_needs_no_confirmation(self):
        self.configuration.poc_reader_backend = "cached_tcp"
        self.configuration.poc_allow_physical_reader_contact = True
        self.configuration.poc_allow_odoo_contact = True
        self.configuration.save()

        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {
                "poc_reader_backend": "fake",
                "confirmation": "",
            },
        )

        self.assertEqual(response.status_code, 302)

        self.configuration.refresh_from_db()

        self.assertEqual(
            self.configuration.poc_reader_backend,
            "fake",
        )
        self.assertFalse(
            self.configuration
            .poc_allow_physical_reader_contact
        )
        self.assertFalse(
            self.configuration.poc_allow_odoo_contact
        )

    def test_readiness_reports_all_configured_items(self):
        ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving door",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.200",
            port=8090,
            device_address=1,
            inventory_mode=ReaderDevice.InventoryMode.CACHED,
            enabled=True,
        )

        self.configuration.poc_reader_backend = "cached_tcp"
        self.configuration.poc_allow_physical_reader_contact = True
        self.configuration.poc_allow_odoo_contact = True
        self.configuration.odoo_base_url = (
            "https://staging-example.odoo.com"
        )
        self.configuration.odoo_database = "staging-db"
        self.configuration.odoo_client_identifier = (
            "rfid@example.com"
        )
        self.configuration.odoo_authentication_method = (
            OperationalConfiguration
            .OdooAuthenticationMethod
            .ODOO_SESSION
        )
        self.configuration.odoo_inventory_count_poc_enabled = True
        self.configuration.odoo_inventory_count_endpoint = (
            "/api/rfid/inventory_count"
        )
        self.configuration.odoo_inventory_count_location_id = 8
        self.configuration.set_odoo_secret(
            "safe-test-secret"
        )
        self.configuration.save()

        self.client.force_login(self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "13/13")
        self.assertContains(
            response,
            "All configuration gates are ready",
        )


@override_settings(
    ALLOW_PHYSICAL_READER_CONTACT=False,
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class DatabasePocControlActionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.staff_user = user_model.objects.create_user(
            username="database-poc-action-staff",
            password="safe-test-password",
            is_staff=True,
        )
        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )

    @patch("bridge_core.views.get_reader_backend")
    def test_database_reader_control_allows_one_shot_test(
        self,
        mocked_get_reader_backend,
    ):
        reader = ReaderDevice.objects.create(
            code="reader-01",
            name="Reader 01",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.200",
            enabled=True,
        )

        self.configuration.poc_reader_backend = "cached_tcp"
        self.configuration.poc_allow_physical_reader_contact = True
        self.configuration.save()

        mocked_backend = (
            mocked_get_reader_backend.return_value
        )
        mocked_backend.read_events.return_value = ()

        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse(
                "bridge_core:reader_validation",
                args=(reader.id,),
            ),
            {
                "scan_seconds": "0",
                "confirmation": (
                    "CONTACT_THIS_READER_ONCE"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        mocked_get_reader_backend.assert_called_once_with(
            "cached_tcp",
            allow_physical_contact=True,
            scan_seconds=0.0,
        )

    @patch(
        "bridge_core.views.execute_odoo_connection_test"
    )
    def test_database_odoo_control_allows_connection_test(
        self,
        mocked_execute,
    ):
        from bridge_core.odoo_connection import (
            OdooConnectionTestResult,
        )

        self.configuration.poc_allow_odoo_contact = True
        self.configuration.save()

        mocked_execute.return_value = (
            OdooConnectionTestResult(
                success=True,
                target_url=(
                    "https://staging-example.odoo.com/"
                    "web/session/get_session_info"
                ),
                response_code="200",
                detail="Mocked success.",
            )
        )

        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse(
                "bridge_core:odoo_connection_test"
            )
        )

        self.assertEqual(response.status_code, 302)
        mocked_execute.assert_called_once_with(
            configuration=self.configuration,
            allow_contact=True,
        )

@override_settings(
    READER_BACKEND="fake",
    ALLOW_PHYSICAL_READER_CONTACT=True,
)
class EffectivePocReaderBackendTests(TestCase):
    def setUp(self):
        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )

    def test_cached_tcp_is_used_for_environment_contact(self):
        from bridge_core.views import (
            effective_poc_reader_backend,
        )

        self.configuration.poc_reader_backend = "fake"
        self.configuration.poc_allow_physical_reader_contact = False

        self.assertEqual(
            effective_poc_reader_backend(
                self.configuration
            ),
            "cached_tcp",
        )

    @override_settings(
        READER_BACKEND="fake",
        ALLOW_PHYSICAL_READER_CONTACT=False,
    )
    def test_fail_closed_environment_keeps_fake_backend(self):
        from bridge_core.views import (
            effective_poc_reader_backend,
        )

        self.configuration.poc_reader_backend = "fake"
        self.configuration.poc_allow_physical_reader_contact = False

        self.assertEqual(
            effective_poc_reader_backend(
                self.configuration
            ),
            "fake",
        )

    def test_database_backend_is_used_for_database_contact(self):
        from bridge_core.views import (
            effective_poc_reader_backend,
        )

        self.configuration.poc_reader_backend = "cached_tcp"
        self.configuration.poc_allow_physical_reader_contact = True

        self.assertEqual(
            effective_poc_reader_backend(
                self.configuration
            ),
            "cached_tcp",
        )
