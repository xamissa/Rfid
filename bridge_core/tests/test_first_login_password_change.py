from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from bridge_core.models import OperationalConfiguration


class FirstDeploymentAuthenticationFlowTests(TestCase):

    def setUp(self):
        user_model = get_user_model()

        self.admin = user_model.objects.create_superuser(
            username="admin",
            email="admin@rfid.local",
            password="admin",
        )

        self.configuration = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )

        self.configuration.setup_completed = False
        self.configuration.setup_completed_at = None
        self.configuration.save()

    def test_anonymous_user_cannot_access_dashboard(self):
        response = self.client.get(
            reverse("bridge_core:dashboard")
        )

        self.assertRedirects(
            response,
            "/accounts/login/?next=/",
            fetch_redirect_response=False,
        )

    def test_default_admin_is_forced_to_custom_password_page(self):
        self.client.login(
            username="admin",
            password="admin",
        )

        response = self.client.get(
            reverse("bridge_core:dashboard")
        )

        self.assertRedirects(
            response,
            reverse("password_change"),
            fetch_redirect_response=False,
        )

        response = self.client.get(
            reverse("password_change")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "Secure administrator account",
        )

    def test_password_change_redirects_to_setup(self):
        self.client.login(
            username="admin",
            password="admin",
        )

        response = self.client.post(
            reverse("password_change"),
            {
                "old_password": "admin",
                "new_password1": "RFID-Bridge-Secure-2026!",
                "new_password2": "RFID-Bridge-Secure-2026!",
            },
        )

        self.assertRedirects(
            response,
            reverse("bridge_core:setup_wizard"),
            fetch_redirect_response=False,
        )

        self.admin.refresh_from_db()

        self.assertFalse(
            self.admin.check_password("admin")
        )

        self.assertTrue(
            self.admin.check_password(
                "RFID-Bridge-Secure-2026!"
            )
        )

    def test_setup_completion_unlocks_dashboard(self):
        self.client.force_login(
            self.admin,
        )

        self.admin.set_password(
            "RFID-Bridge-Secure-2026!"
        )
        self.admin.save()

        self.configuration.setup_completed = False
        self.configuration.save()

        response = self.client.get(
            reverse("bridge_core:setup_wizard")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        response = self.client.post(
            reverse("bridge_core:setup_wizard")
        )

        self.assertRedirects(
            response,
            reverse("bridge_core:dashboard"),
            fetch_redirect_response=False,
        )

        self.configuration.refresh_from_db()

        self.assertTrue(
            self.configuration.setup_completed
        )

        response = self.client.get(
            reverse("bridge_core:dashboard")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    def test_other_staff_users_are_not_forced(self):
        user_model = get_user_model()

        operator = user_model.objects.create_user(
            username="operator",
            password="operator-password-2026",
            is_staff=True,
        )

        self.client.force_login(operator)

        response = self.client.get(
            reverse("bridge_core:dashboard")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    def test_logout_remains_available(self):
        self.client.login(
            username="admin",
            password="admin",
        )

        response = self.client.post(
            reverse("logout")
        )

        self.assertEqual(
            response.status_code,
            302,
        )
