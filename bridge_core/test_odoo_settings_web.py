from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from bridge_core.models import OperationalConfiguration


@override_settings(
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class OdooIntegrationSettingsWebTests(TestCase):
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
        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )
        self.url = reverse(
            "bridge_core:odoo_integration_settings"
        )

    def valid_post_data(self, **overrides):
        values = {
            "odoo_base_url": (
                "https://staging-example.odoo.com"
            ),
            "odoo_database": "staging-db",
            "odoo_session_endpoint": "/rfid/session",
            "odoo_event_endpoint": "/rfid/events",
            "odoo_authentication_method": (
                OperationalConfiguration
                .OdooAuthenticationMethod.BEARER_TOKEN
            ),
            "odoo_client_identifier": "rfid-bridge",
            "odoo_secret": "staging-token-value",
            "odoo_request_timeout_seconds": "15",
            "odoo_verify_tls": "on",
        }
        values.update(overrides)
        return values

    def test_anonymous_user_is_redirected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_nonstaff_user_cannot_access_page(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_staff_get_does_not_display_secret(self):
        self.configuration.set_odoo_secret(
            "existing-secret-value"
        )
        self.configuration.save()

        self.client.force_login(self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Present and encrypted",
        )
        self.assertNotContains(
            response,
            "existing-secret-value",
        )
        self.assertContains(
            response,
            "Odoo contact is blocked by the staff-only POC control",
        )

    @patch("bridge_core.views.get_reader_backend")
    def test_save_does_not_contact_reader_or_odoo(
        self,
        mocked_get_reader_backend,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_post_data(),
        )

        self.assertEqual(response.status_code, 302)
        mocked_get_reader_backend.assert_not_called()

        self.configuration.refresh_from_db()

        self.assertEqual(
            self.configuration.odoo_base_url,
            "https://staging-example.odoo.com",
        )
        self.assertFalse(
            self.configuration.odoo_integration_enabled
        )
        self.assertTrue(self.configuration.odoo_verify_tls)
        self.assertTrue(self.configuration.has_odoo_secret)
        self.assertEqual(
            self.configuration.get_odoo_secret(),
            "staging-token-value",
        )
        self.assertNotEqual(
            self.configuration.odoo_secret_encrypted,
            "staging-token-value",
        )

    def test_blank_secret_preserves_existing_secret(self):
        self.configuration.set_odoo_secret(
            "existing-secret-value"
        )
        self.configuration.save()

        original_ciphertext = (
            self.configuration.odoo_secret_encrypted
        )

        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_post_data(odoo_secret=""),
        )

        self.assertEqual(response.status_code, 302)

        self.configuration.refresh_from_db()

        self.assertEqual(
            self.configuration.odoo_secret_encrypted,
            original_ciphertext,
        )
        self.assertEqual(
            self.configuration.get_odoo_secret(),
            "existing-secret-value",
        )

    def test_clear_secret_removes_existing_secret(self):
        self.configuration.set_odoo_secret(
            "existing-secret-value"
        )
        self.configuration.save()

        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_post_data(
                odoo_authentication_method=(
                    OperationalConfiguration
                    .OdooAuthenticationMethod.NONE
                ),
                odoo_secret="",
                clear_odoo_secret="on",
            ),
        )

        self.assertEqual(response.status_code, 302)

        self.configuration.refresh_from_db()

        self.assertFalse(self.configuration.has_odoo_secret)

    def test_integration_enable_requires_endpoints(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_post_data(
                odoo_session_endpoint="",
                odoo_event_endpoint="",
                odoo_integration_enabled="on",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "required when integration is enabled",
            count=2,
        )

        self.configuration.refresh_from_db()

        self.assertFalse(
            self.configuration.odoo_integration_enabled
        )

    def test_basic_auth_requires_client_identifier(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_post_data(
                odoo_authentication_method=(
                    OperationalConfiguration
                    .OdooAuthenticationMethod.BASIC
                ),
                odoo_client_identifier="",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            (
                "A username or client identifier is required "
                "for the selected authentication method."
            ),
        )

    def test_secret_never_appears_after_invalid_post(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_post_data(
                odoo_secret="do-not-render-this-secret",
                odoo_request_timeout_seconds="999",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "do-not-render-this-secret",
        )

        self.configuration.refresh_from_db()

        self.assertFalse(self.configuration.has_odoo_secret)


@override_settings(
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class OdooConnectionTestWebTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.staff_user = user_model.objects.create_user(
            username="connection-test-staff",
            password="safe-test-password",
            is_staff=True,
        )
        self.normal_user = user_model.objects.create_user(
            username="connection-test-normal",
            password="safe-test-password",
            is_staff=False,
        )
        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )
        self.url = reverse(
            "bridge_core:odoo_connection_test"
        )

    def test_get_is_not_allowed(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_nonstaff_user_cannot_run_test(self):
        self.client.force_login(self.normal_user)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

    @patch(
        "bridge_core.views.execute_odoo_connection_test"
    )
    def test_global_contact_guard_blocks_before_client(
        self,
        mocked_execute_test,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No network request was made",
        )
        mocked_execute_test.assert_not_called()

    @override_settings(
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.views.execute_odoo_connection_test"
    )
    def test_success_does_not_create_delivery_attempts(
        self,
        mocked_execute_test,
    ):
        from bridge_core.models import DeliveryAttempt
        from bridge_core.odoo_connection import (
            OdooConnectionTestResult,
        )

        mocked_execute_test.return_value = (
            OdooConnectionTestResult(
                success=True,
                target_url=(
                    "https://staging-example.odoo.com/"
                    "rfid/session"
                ),
                response_code="200",
                detail="Safe test success.",
            )
        )

        self.client.force_login(self.staff_user)

        before_count = DeliveryAttempt.objects.count()

        response = self.client.post(
            self.url,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No RFID event was delivered",
        )
        self.assertEqual(
            DeliveryAttempt.objects.count(),
            before_count,
        )
        mocked_execute_test.assert_called_once_with(
            configuration=self.configuration,
            allow_contact=True,
        )


@override_settings(
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class OdooInventoryCountPocSettingsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="poc-settings-staff",
            password="safe-test-password",
            is_staff=True,
        )
        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )
        self.url = reverse(
            "bridge_core:odoo_integration_settings"
        )

    def valid_poc_data(self, **overrides):
        values = {
            "odoo_base_url": (
                "https://staging-example.odoo.com"
            ),
            "odoo_database": "staging-database",
            "odoo_session_endpoint": "",
            "odoo_event_endpoint": "",
            "odoo_authentication_method": (
                OperationalConfiguration
                .OdooAuthenticationMethod.ODOO_SESSION
            ),
            "odoo_client_identifier": (
                "rfid.integration@example.com"
            ),
            "odoo_secret": "staging-password",
            "odoo_request_timeout_seconds": "15",
            "odoo_verify_tls": "on",
            "odoo_inventory_count_poc_enabled": "on",
            "odoo_inventory_count_endpoint": (
                "/api/rfid/inventory_count"
            ),
            "odoo_inventory_count_location_id": "8",
        }
        values.update(overrides)
        return values

    def test_valid_poc_configuration_is_saved_encrypted(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_poc_data(),
        )

        self.assertEqual(response.status_code, 302)

        self.configuration.refresh_from_db()

        self.assertTrue(
            self.configuration
            .odoo_inventory_count_poc_enabled
        )
        self.assertEqual(
            self.configuration
            .odoo_inventory_count_endpoint,
            "/api/rfid/inventory_count",
        )
        self.assertEqual(
            self.configuration
            .odoo_inventory_count_location_id,
            8,
        )
        self.assertEqual(
            self.configuration.odoo_authentication_method,
            OperationalConfiguration
            .OdooAuthenticationMethod.ODOO_SESSION,
        )
        self.assertTrue(self.configuration.has_odoo_secret)
        self.assertNotEqual(
            self.configuration.odoo_secret_encrypted,
            "staging-password",
        )

    def test_poc_requires_odoo_session_authentication(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_poc_data(
                odoo_authentication_method=(
                    OperationalConfiguration
                    .OdooAuthenticationMethod.BEARER_TOKEN
                ),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            (
                "Bhumika&#x27;s inventory-count POC "
                "requires Odoo username and password "
                "session authentication."
            ),
        )

        self.configuration.refresh_from_db()
        self.assertFalse(
            self.configuration
            .odoo_inventory_count_poc_enabled
        )

    def test_poc_requires_staging_location_id(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            self.valid_poc_data(
                odoo_inventory_count_location_id="",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "A staging location ID is required",
        )

    def test_disabled_poc_preserves_fail_closed_default(self):
        self.client.force_login(self.staff_user)

        data = self.valid_poc_data()
        data.pop("odoo_inventory_count_poc_enabled")

        response = self.client.post(
            self.url,
            data,
        )

        self.assertEqual(response.status_code, 302)

        self.configuration.refresh_from_db()
        self.assertFalse(
            self.configuration
            .odoo_inventory_count_poc_enabled
        )


@override_settings(
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class OdooInventoryCountPocManualWebTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.staff_user = user_model.objects.create_user(
            username="poc-manual-staff",
            password="safe-test-password",
            is_staff=True,
        )
        self.normal_user = user_model.objects.create_user(
            username="poc-manual-normal",
            password="safe-test-password",
            is_staff=False,
        )
        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )
        self.url = reverse(
            "bridge_core:"
            "odoo_inventory_count_poc_manual_test"
        )

    def test_get_is_not_allowed(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_nonstaff_user_cannot_run_test(self):
        self.client.force_login(self.normal_user)

        response = self.client.post(
            self.url,
            {"rfid_tags": "EPC001"},
        )

        self.assertEqual(response.status_code, 302)

    @patch(
        "bridge_core.views.execute_inventory_count_poc"
    )
    def test_global_guard_blocks_before_client(
        self,
        mocked_execute,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {"rfid_tags": "EPC001"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No network request was made",
        )
        mocked_execute.assert_not_called()

    @patch(
        "bridge_core.views.execute_inventory_count_poc"
    )
    def test_empty_tags_block_before_client(
        self,
        mocked_execute,
    ):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {"rfid_tags": "  \n ,  "},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "provide at least one valid RFID tag",
        )
        mocked_execute.assert_not_called()

    @override_settings(
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.views.execute_inventory_count_poc"
    )
    def test_success_passes_tags_without_creating_queue_records(
        self,
        mocked_execute,
    ):
        from bridge_core.models import (
            DeliveryAttempt,
            RawRFIDEvent,
        )
        from bridge_core.odoo_inventory_count_poc import (
            OdooInventoryCountPocResult,
        )

        mocked_execute.return_value = (
            OdooInventoryCountPocResult(
                success=True,
                target_url=(
                    "https://staging-example.odoo.com/"
                    "api/rfid/inventory_count"
                ),
                response_code="200",
                total_counted=2,
                unknown_rfid_tags=("UNKNOWN1",),
                detail="Safe mocked success.",
            )
        )

        self.client.force_login(self.staff_user)

        before_events = RawRFIDEvent.objects.count()
        before_attempts = DeliveryAttempt.objects.count()

        response = self.client.post(
            self.url,
            {
                "rfid_tags": (
                    "epc001\n"
                    "EPC002, EPC001"
                ),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Odoo counted 2 tag(s)",
        )
        self.assertContains(
            response,
            "UNKNOWN1",
        )
        self.assertEqual(
            RawRFIDEvent.objects.count(),
            before_events,
        )
        self.assertEqual(
            DeliveryAttempt.objects.count(),
            before_attempts,
        )

        mocked_execute.assert_called_once_with(
            configuration=self.configuration,
            rfid_tags=(
                "epc001",
                "EPC002",
                "EPC001",
            ),
            allow_contact=True,
        )

    @override_settings(
        ALLOW_ODOO_CONTACT=True,
        SENDER_BACKEND="disabled",
    )
    @patch(
        "bridge_core.views.execute_inventory_count_poc"
    )
    def test_failed_result_is_reported_without_secret(
        self,
        mocked_execute,
    ):
        from bridge_core.odoo_inventory_count_poc import (
            OdooInventoryCountPocResult,
        )

        mocked_execute.return_value = (
            OdooInventoryCountPocResult(
                success=False,
                target_url=(
                    "https://staging-example.odoo.com/"
                    "api/rfid/inventory_count"
                ),
                response_code="403",
                error_kind="HTTPError",
                detail="Odoo rejected the request.",
            )
        )

        self.configuration.set_odoo_secret(
            "do-not-render-this-password"
        )
        self.configuration.save()

        self.client.force_login(self.staff_user)

        response = self.client.post(
            self.url,
            {"rfid_tags": "EPC001"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "HTTP 403",
        )
        self.assertNotContains(
            response,
            "do-not-render-this-password",
        )
