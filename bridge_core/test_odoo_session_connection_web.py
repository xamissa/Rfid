from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from bridge_core.models import (
    DeliveryAttempt,
    OperationalConfiguration,
)
from bridge_core.odoo_connection import OdooConnectionTestResult


@override_settings(
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class OdooSessionConnectionBlockedWebTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="odoo-session-blocked-staff",
            password="safe-test-password",
            is_staff=True,
        )

        self.configuration = OperationalConfiguration.objects.get(
            name="default"
        )
        self.configuration.setup_completed = True
        self.configuration.poc_allow_odoo_contact = False
        self.configuration.save(
            update_fields=(
                "setup_completed",
                "poc_allow_odoo_contact",
                "updated_at",
            )
        )

        self.url = reverse(
            "bridge_core:odoo_connection_test"
        )

    def test_get_is_rejected_as_post_only(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    @patch(
        "bridge_core.views.execute_odoo_connection_test"
    )
    def test_blocked_contact_makes_no_client_call(
        self,
        mocked_execute,
    ):
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No network request was made",
        )
        mocked_execute.assert_not_called()


@override_settings(
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class OdooSessionConnectionAllowedWebTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="odoo-session-allowed-staff",
            password="safe-test-password",
            is_staff=True,
        )

        self.configuration = OperationalConfiguration.objects.get(
            name="default"
        )
        self.configuration.setup_completed = True
        self.configuration.poc_allow_odoo_contact = True
        self.configuration.save(
            update_fields=(
                "setup_completed",
                "poc_allow_odoo_contact",
                "updated_at",
            )
        )

        self.url = reverse(
            "bridge_core:odoo_connection_test"
        )

    @patch(
        "bridge_core.views.execute_odoo_connection_test"
    )
    def test_success_reports_session_authentication_without_delivery(
        self,
        mocked_execute,
    ):
        mocked_execute.return_value = OdooConnectionTestResult(
            success=True,
            target_url=(
                "https://staging-example.odoo.com/"
                "web/session/authenticate"
            ),
            response_code="200",
            detail=(
                "Odoo session authentication succeeded. "
                "No RFID event or inventory request was delivered."
            ),
        )

        self.client.force_login(self.user)

        before_attempts = DeliveryAttempt.objects.count()

        response = self.client.post(
            self.url,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Odoo connection test succeeded with HTTP 200",
        )
        self.assertContains(
            response,
            "No RFID event was delivered",
        )
        self.assertEqual(
            DeliveryAttempt.objects.count(),
            before_attempts,
        )

        mocked_execute.assert_called_once_with(
            configuration=self.configuration,
            allow_contact=True,
        )
