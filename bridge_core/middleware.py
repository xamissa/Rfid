from django.shortcuts import redirect
from django.urls import Resolver404, resolve


class RequireInitialAdminPasswordChangeMiddleware:
    """
    Protects a newly deployed RFID Bridge installation.

    Flow:
    1. Force factory admin password change.
    2. Force initial setup wizard completion.
    3. Allow normal operation afterwards.
    """

    default_username = "admin"
    default_password = "admin"

    password_change_allowed_url_names = {
        "login",
        "logout",
        "password_change",
    }

    setup_allowed_url_names = {
        "login",
        "logout",
        "password_change",
        "setup_wizard",

        # Deployment configuration pages
        "dashboard",
        "reader_list",
        "reader_create",
        "reader_update",
        "reader_validation",
        "odoo_integration_settings",
        "operational_settings",
        "poc_control_centre",

        # Operational visibility pages
        "session_list",
        "event_list",
        "delivery_attempt_list",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if self._password_change_required(user):
            return self._redirect_unless_allowed(
                request,
                "password_change",
                self.password_change_allowed_url_names,
            )

        if self._setup_required(user):
            return self._redirect_unless_allowed(
                request,
                "bridge_core:setup_wizard",
                self.setup_allowed_url_names,
            )

        return self.get_response(request)

    def _redirect_unless_allowed(
        self,
        request,
        destination,
        allowed_url_names,
    ):
        try:
            match = resolve(request.path_info)
            url_name = match.url_name
        except Resolver404:
            url_name = None

        if url_name not in allowed_url_names:
            return redirect(destination)

        return self.get_response(request)

    def _password_change_required(self, user):
        return bool(
            user
            and user.is_authenticated
            and user.get_username() == self.default_username
            and user.check_password(self.default_password)
        )

    def _setup_required(self, user):
        if not user or not user.is_authenticated:
            return False

        from bridge_core.models import OperationalConfiguration

        configuration = OperationalConfiguration.objects.get(
            name="default"
        )

        return not configuration.setup_completed
