from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    ActiveSessionScanForm,
    InitialPasswordChangeForm,
    OdooIntegrationConfigurationForm,
    OdooInventoryCountPocManualForm,
    OperationalConfigurationForm,
    PhysicalReaderTestForm,
    PocRuntimeControlForm,
    ReaderDeviceForm,
)
from .models import (
    DeliveryAttempt,
    OperationalConfiguration,
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)
from .odoo_connection import (
    OdooConnectionConfigurationError,
    execute_odoo_connection_test,
)
from .odoo_inventory_count_poc import (
    OdooInventoryCountPocConfigurationError,
    execute_inventory_count_poc,
)
from .reader_backends import get_reader_backend


class InitialAdminLoginView(auth_views.LoginView):
    """
    Redirect factory administrator directly to password change.

    Prevents Django's normal LOGIN_REDIRECT_URL from briefly
    exposing the dashboard before the first password change.
    """

    def get_success_url(self):
        if (
            self.request.user.get_username() == "admin"
            and self.request.user.check_password("admin")
        ):
            return "/accounts/password-change/"

        return super().get_success_url()


def build_system_readiness_checks(
    configuration,
    *,
    reader_device_count=None,
    enabled_reader_device_count=None,
):
    if reader_device_count is None:
        reader_device_count = ReaderDevice.objects.count()

    if enabled_reader_device_count is None:
        enabled_reader_device_count = (
            ReaderDevice.objects.filter(
                enabled=True,
            ).count()
        )
    checks = (
        {
            "key": "reader_configured",
            "label": "Reader configured",
            "ready": reader_device_count > 0,
            "area": "RFID hardware",
        },
        {
            "key": "enabled_reader",
            "label": "Enabled reader configured",
            "ready": enabled_reader_device_count > 0,
            "area": "RFID hardware",
        },
        {
            "key": "physical_reader_backend",
            "label": "Physical RFID reader backend selected",
            "ready": (
                configuration.poc_reader_backend
                in {
                    OperationalConfiguration
                    .PocReaderBackend
                    .CACHED_TCP,
                    OperationalConfiguration
                    .PocReaderBackend
                    .ACTIVE_TCP,
                }
            ),
            "area": "RFID hardware",
        },
        {
            "key": "reader_contact",
            "label": "Physical reader contact approved",
            "ready": (
                configuration
                .poc_allow_physical_reader_contact
            ),
            "area": "RFID hardware",
        },
        {
            "key": "odoo_url",
            "label": "Odoo.sh staging URL configured",
            "ready": bool(
                configuration.odoo_base_url.strip()
            ),
            "area": "Odoo.sh staging",
        },
        {
            "key": "odoo_database",
            "label": "Odoo.sh staging database configured",
            "ready": bool(
                configuration.odoo_database.strip()
            ),
            "area": "Odoo.sh staging",
        },
        {
            "key": "odoo_username",
            "label": "Odoo username configured",
            "ready": bool(
                configuration
                .odoo_client_identifier
                .strip()
            ),
            "area": "Odoo.sh staging",
        },
        {
            "key": "odoo_credential",
            "label": "Odoo credential stored encrypted",
            "ready": configuration.has_odoo_secret,
            "area": "Odoo.sh staging",
        },
        {
            "key": "odoo_session_auth",
            "label": "Odoo session authentication selected",
            "ready": (
                configuration.odoo_authentication_method
                == OperationalConfiguration
                .OdooAuthenticationMethod
                .ODOO_SESSION
            ),
            "area": "Odoo.sh staging",
        },
        {
            "key": "inventory_poc_enabled",
            "label": "Inventory validation enabled",
            "ready": (
                configuration
                .odoo_inventory_count_poc_enabled
            ),
            "area": "Inventory validation",
        },
        {
            "key": "inventory_endpoint",
            "label": "Inventory-count endpoint configured",
            "ready": bool(
                configuration
                .odoo_inventory_count_endpoint
                .strip()
            ),
            "area": "Inventory validation",
        },
        {
            "key": "inventory_location",
            "label": "Staging inventory location configured",
            "ready": (
                configuration
                .odoo_inventory_count_location_id
                is not None
            ),
            "area": "Inventory validation",
        },
        {
            "key": "odoo_contact",
            "label": "Odoo.sh staging contact approved",
            "ready": configuration.poc_allow_odoo_contact,
            "area": "Inventory validation",
        },
    )

    ready_count = sum(
        1 for check in checks if check["ready"]
    )

    return {
        "checks": checks,
        "ready_count": ready_count,
        "total_count": len(checks),
        "fully_ready": ready_count == len(checks),
    }


def reader_contact_allowed(configuration):
    return bool(
        configuration.poc_allow_physical_reader_contact
        or settings.ALLOW_PHYSICAL_READER_CONTACT
    )


def effective_reader_backend(configuration):
    if configuration.poc_allow_physical_reader_contact:
        return configuration.poc_reader_backend

    if settings.ALLOW_PHYSICAL_READER_CONTACT:
        return (
            OperationalConfiguration
            .PocReaderBackend
            .CACHED_TCP
        )

    return settings.READER_BACKEND


def effective_poc_reader_backend(configuration):
    """
    Backwards-compatible name for the controlled POC reader selector.

    Existing callers and tests may still use the original helper name.
    """
    return effective_reader_backend(configuration)


def odoo_contact_allowed(configuration):
    return bool(
        configuration.poc_allow_odoo_contact
        or settings.ALLOW_ODOO_CONTACT
    )


def build_dashboard_context():
    configuration = OperationalConfiguration.objects.get(
        name="default"
    )

    reader_device_count = ReaderDevice.objects.count()
    enabled_reader_device_count = (
        ReaderDevice.objects.filter(
            enabled=True,
        ).count()
    )
    disabled_reader_device_count = (
        ReaderDevice.objects.filter(
            enabled=False,
        ).count()
    )

    readiness = build_system_readiness_checks(
        configuration,
        reader_device_count=reader_device_count,
        enabled_reader_device_count=(
            enabled_reader_device_count
        ),
    )

    queue_state_counts = {
        state_value: RawRFIDEvent.objects.filter(
            queue_state=state_value,
        ).count()
        for state_value, _state_label in RawRFIDEvent.QueueState.choices
    }

    return {
        "setup_completed": configuration.setup_completed,
        "setup_completed_at": configuration.setup_completed_at,
        "reader_backend": settings.READER_BACKEND,
        "reader_backend_configured": (
            bool(configuration.poc_reader_backend)
        ),
        "reader_contact_allowed": (
            reader_contact_allowed(configuration)
        ),
        "odoo_contact_allowed": (
            odoo_contact_allowed(configuration)
        ),
        "system_readiness": readiness,
        "sender_backend": settings.SENDER_BACKEND,
        "allow_physical_reader_contact": (
            settings.ALLOW_PHYSICAL_READER_CONTACT
        ),
        "allow_odoo_contact": settings.ALLOW_ODOO_CONTACT,
        "reader_device_count": reader_device_count,
        "enabled_reader_device_count": (
            enabled_reader_device_count
        ),
        "disabled_reader_device_count": (
            disabled_reader_device_count
        ),
        "raw_event_count": RawRFIDEvent.objects.count(),
        "queue_state_counts": queue_state_counts,
        "delivery_attempt_count": DeliveryAttempt.objects.count(),
        "operational_configuration_count": (
            OperationalConfiguration.objects.count()
        ),
    }


@login_required
def initial_password_change(request):
    if request.method == "POST":
        form = InitialPasswordChangeForm(
            request.user,
            request.POST,
        )

        if form.is_valid():
            request.user.set_password(
                form.cleaned_data["new_password1"]
            )
            request.user.save()

            update_session_auth_hash(
                request,
                request.user,
            )

            return redirect(
                "bridge_core:setup_wizard"
            )

    else:
        form = InitialPasswordChangeForm(
            request.user,
        )

    return render(
        request,
        "bridge_core/initial_password_change.html",
        {
            "form": form,
        },
    )

def build_setup_readiness_context(readiness):
    required_keys = {
        "reader_configured",
        "enabled_reader",
        "odoo_url",
        "odoo_database",
        "odoo_username",
        "odoo_credential",
        "odoo_session_auth",
    }

    safety_keys = {
        "reader_contact",
        "odoo_contact",
        "inventory_poc_enabled",
        "inventory_endpoint",
        "inventory_location",
    }

    return {
        "required_checks": [
            check
            for check in readiness["checks"]
            if check["key"] in required_keys
        ],
        "safety_checks": [
            check
            for check in readiness["checks"]
            if check["key"] in safety_keys
        ],
    }


@login_required
def setup_wizard(request):
    from django.utils import timezone

    configuration = OperationalConfiguration.objects.get(
        name="default"
    )

    readiness = build_system_readiness_checks(
        configuration
    )

    setup_readiness = build_setup_readiness_context(
        readiness
    )

    deployment_ready = all(
        check["ready"]
        for check in setup_readiness["required_checks"]
    )

    if request.method == "POST" and deployment_ready:
        configuration.setup_completed = True
        configuration.setup_completed_at = timezone.now()
        configuration.save(
            update_fields=[
                "setup_completed",
                "setup_completed_at",
                "updated_at",
            ]
        )

        return redirect("bridge_core:dashboard")

    return render(
        request,
        "bridge_core/setup_wizard.html",
        {
            "configuration": configuration,
            "readiness": readiness,
            "setup_readiness": setup_readiness,
            "deployment_ready": deployment_ready,
        },
    )


@login_required
def dashboard(request):
    return render(
        request,
        "bridge_core/dashboard.html",
        build_dashboard_context(),
    )

@login_required
def reader_list(request):
    return render(
        request,
        "bridge_core/reader_list.html",
        {
            "readers": ReaderDevice.objects.all(),
            "allow_physical_reader_contact": (
                settings.ALLOW_PHYSICAL_READER_CONTACT
            ),
        },
    )


@login_required
def reader_create(request):
    if request.method == "POST":
        form = ReaderDeviceForm(request.POST)

        if form.is_valid():
            reader = form.save()
            messages.success(
                request,
                f"Reader {reader.code} was created.",
            )
            return redirect("bridge_core:reader_list")
    else:
        form = ReaderDeviceForm()

    return render(
        request,
        "bridge_core/reader_form.html",
        {
            "form": form,
            "page_title": "Add reader",
            "submit_label": "Create reader",
        },
    )


@login_required
def reader_update(request, reader_id):
    reader = get_object_or_404(ReaderDevice, pk=reader_id)

    if request.method == "POST":
        form = ReaderDeviceForm(request.POST, instance=reader)

        if form.is_valid():
            reader = form.save()
            messages.success(
                request,
                f"Reader {reader.code} was updated.",
            )
            return redirect("bridge_core:reader_list")
    else:
        form = ReaderDeviceForm(instance=reader)

    return render(
        request,
        "bridge_core/reader_form.html",
        {
            "form": form,
            "page_title": f"Edit {reader.code}",
            "submit_label": "Save changes",
            "reader": reader,
        },
    )


@staff_member_required(login_url="login")
def reader_validation(request, reader_id):
    reader = get_object_or_404(
        ReaderDevice,
        pk=reader_id,
    )
    technical_reads = None
    configuration = OperationalConfiguration.objects.get(
        name="default"
    )
    allow_reader_contact = reader_contact_allowed(
        configuration
    )
    allow_odoo_contact = odoo_contact_allowed(
        configuration
    )

    if request.method == "POST":
        form = PhysicalReaderTestForm(request.POST)

        if form.is_valid():
            if not reader.enabled:
                form.add_error(
                    None,
                    "The reader must be enabled before testing.",
                )
            elif (
                reader.inventory_mode
                != ReaderDevice.InventoryMode.CACHED
            ):
                form.add_error(
                    None,
                    "Web testing currently supports cached "
                    "inventory mode only.",
                )
            elif not allow_reader_contact:
                form.add_error(
                    None,
                    "Physical reader contact is disabled by "
                    "the gateway configuration.",
                )
            elif allow_odoo_contact:
                form.add_error(
                    None,
                    "Odoo contact must remain disabled during "
                    "reader testing.",
                )
            elif settings.SENDER_BACKEND != "disabled":
                form.add_error(
                    None,
                    "The sender backend must remain disabled "
                    "during reader testing.",
                )
            else:
                try:
                    backend = get_reader_backend(
                        effective_reader_backend(
                            configuration
                        ),
                        allow_physical_contact=True,
                        scan_seconds=form.cleaned_data[
                            "scan_seconds"
                        ],
                    )
                    technical_reads = tuple(
                        backend.read_events(device=reader)
                    )
                except Exception as exc:
                    form.add_error(
                        None,
                        f"Physical reader test failed: {exc}",
                    )
    else:
        form = PhysicalReaderTestForm()

    return render(
        request,
        "bridge_core/reader_validation.html",
        {
            "reader": reader,
            "form": form,
            "technical_reads": technical_reads,
            "allow_physical_reader_contact": (
                allow_reader_contact
            ),
            "allow_odoo_contact": allow_odoo_contact,
            "reader_backend_configured": (
                effective_reader_backend(
                    configuration
                )
            ),
            "sender_backend": settings.SENDER_BACKEND,
        },
    )


@staff_member_required(login_url="login")
def poc_control_centre(request):
    configuration = OperationalConfiguration.objects.get(
        name="default"
    )

    if request.method == "POST":
        form = PocRuntimeControlForm(
            request.POST,
            instance=configuration,
        )

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "POC controls were updated. No reader or Odoo "
                "connection was made, and the worker was not started.",
            )
            return redirect(
                "bridge_core:poc_control_centre"
            )
    else:
        form = PocRuntimeControlForm(
            instance=configuration
        )

    readiness = build_system_readiness_checks(
        configuration
    )

    return render(
        request,
        "bridge_core/poc_control_centre.html",
        {
            "configuration": configuration,
            "form": form,
            "readiness": readiness,
            "environment_reader_backend": (
                settings.READER_BACKEND
            ),
            "environment_sender_backend": (
                settings.SENDER_BACKEND
            ),
            "environment_reader_contact": (
                settings
                .ALLOW_PHYSICAL_READER_CONTACT
            ),
            "environment_odoo_contact": (
                settings.ALLOW_ODOO_CONTACT
            ),
            "worker_active": False,
        },
    )


@login_required
def operational_settings(request):
    configuration = OperationalConfiguration.objects.get(name="default")

    if request.method == "POST":
        form = OperationalConfigurationForm(
            request.POST,
            instance=configuration,
        )

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Operational settings were updated.",
            )
            return redirect("bridge_core:operational_settings")
    else:
        form = OperationalConfigurationForm(instance=configuration)

    return render(
        request,
        "bridge_core/operational_settings.html",
        {
            "form": form,
            "configuration": configuration,
        },
    )

@staff_member_required(login_url="login")
def odoo_integration_settings(request):
    configuration = OperationalConfiguration.objects.get(
        name="default"
    )

    if request.method == "POST":
        form = OdooIntegrationConfigurationForm(
            request.POST,
            instance=configuration,
        )

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Odoo integration settings were updated. "
                "No connection test or event delivery was performed.",
            )
            return redirect(
                "bridge_core:odoo_integration_settings"
            )
    else:
        form = OdooIntegrationConfigurationForm(
            instance=configuration,
        )

    return render(
        request,
        "bridge_core/odoo_integration_settings.html",
        {
            "form": form,
            "configuration": configuration,
            "poc_manual_form": OdooInventoryCountPocManualForm(),
            "allow_odoo_contact": odoo_contact_allowed(
                configuration
            ),
            "sender_backend": settings.SENDER_BACKEND,
            "readiness": build_system_readiness_checks(
                configuration
            ),
        },
    )


@staff_member_required(login_url="login")
@require_POST
def odoo_connection_test(request):
    configuration = OperationalConfiguration.objects.get(
        name="default"
    )

    if not odoo_contact_allowed(
        configuration
    ):
        messages.error(
            request,
            "Odoo contact is disabled by the gateway configuration. "
            "No network request was made.",
        )
        return redirect(
            "bridge_core:odoo_integration_settings"
        )

    try:
        result = execute_odoo_connection_test(
            configuration=configuration,
            allow_contact=True,
        )
    except (
        OdooConnectionConfigurationError,
        PermissionError,
    ) as exc:
        messages.error(
            request,
            f"Odoo connection test blocked: {exc}",
        )
        return redirect(
            "bridge_core:odoo_integration_settings"
        )

    if result.success:
        messages.success(
            request,
            "Odoo connection test succeeded with HTTP "
            f"{result.response_code}. No RFID event was delivered.",
        )
    else:
        response_detail = (
            f" HTTP {result.response_code}."
            if result.response_code
            else ""
        )
        messages.error(
            request,
            "Odoo connection test failed."
            f"{response_detail} {result.detail}",
        )

    return redirect(
        "bridge_core:odoo_integration_settings"
    )


@staff_member_required(login_url="login")
@require_POST
def odoo_inventory_count_poc_manual_test(request):
    configuration = OperationalConfiguration.objects.get(
        name="default"
    )

    form = OdooInventoryCountPocManualForm(request.POST)

    if not form.is_valid():
        messages.error(
            request,
            "Inventory validation request blocked: "
            "provide at least one valid RFID tag.",
        )
        return redirect(
            "bridge_core:odoo_integration_settings"
        )

    if not odoo_contact_allowed(
        configuration
    ):
        messages.error(
            request,
            "Inventory validation request blocked because Odoo connectivity "
            "is globally disabled. No network request was made.",
        )
        return redirect(
            "bridge_core:odoo_integration_settings"
        )

    try:
        result = execute_inventory_count_poc(
            configuration=configuration,
            rfid_tags=form.cleaned_data["rfid_tags"],
            allow_contact=True,
        )
    except (
        OdooInventoryCountPocConfigurationError,
        PermissionError,
    ) as exc:
        messages.error(
            request,
            f"Inventory validation request blocked: {exc}",
        )
        return redirect(
            "bridge_core:odoo_integration_settings"
        )

    if result.success:
        unknown_detail = ""

        if result.unknown_rfid_tags:
            unknown_detail = (
                " Unknown RFID tags: "
                + ", ".join(result.unknown_rfid_tags)
                + "."
            )

        messages.success(
            request,
            "Inventory validation succeeded. "
            f"Odoo counted {result.total_counted} tag(s)."
            f"{unknown_detail}",
        )
    else:
        response_detail = (
            f" HTTP {result.response_code}."
            if result.response_code
            else ""
        )

        messages.error(
            request,
            "Inventory validation failed."
            f"{response_detail} {result.detail}",
        )

    return redirect(
        "bridge_core:odoo_integration_settings"
    )


@login_required
def session_list(request):
    sessions = (
        RFIDSession.objects
        .select_related("device")
        .all()[:200]
    )

    configuration = OperationalConfiguration.objects.get(
        name="default"
    )

    return render(
        request,
        "bridge_core/session_list.html",
        {
            "sessions": sessions,
            "scan_form": ActiveSessionScanForm(),
            "active_tcp_selected": (
                configuration.poc_reader_backend
                == OperationalConfiguration
                .PocReaderBackend
                .ACTIVE_TCP
            ),
            "reader_contact_allowed": (
                reader_contact_allowed(configuration)
            ),
            "odoo_contact_allowed": (
                odoo_contact_allowed(configuration)
            ),
            "sender_backend": settings.SENDER_BACKEND,
        },
    )


@staff_member_required(login_url="login")
@require_POST
def active_session_scan(request, session_id):
    session = get_object_or_404(
        RFIDSession.objects.select_related("device"),
        pk=session_id,
    )
    configuration = OperationalConfiguration.objects.get(
        name="default"
    )
    form = ActiveSessionScanForm(request.POST)

    if session.status != RFIDSession.Status.ACTIVE:
        messages.error(
            request,
            "The RFID session is not active. "
            "The reader was not contacted.",
        )
        return redirect("bridge_core:session_list")

    if not form.is_valid():
        messages.error(
            request,
            "Active RFID scan blocked: "
            "check the duration and confirmation phrase.",
        )
        return redirect("bridge_core:session_list")

    if (
        configuration.poc_reader_backend
        != OperationalConfiguration
        .PocReaderBackend
        .ACTIVE_TCP
    ):
        messages.error(
            request,
            "Active RFID scan blocked because Active TCP "
            "is not selected in System configuration.",
        )
        return redirect("bridge_core:session_list")

    if not reader_contact_allowed(configuration):
        messages.error(
            request,
            "Active RFID scan blocked because physical "
            "reader contact is disabled.",
        )
        return redirect("bridge_core:session_list")

    if odoo_contact_allowed(configuration):
        messages.error(
            request,
            "Active RFID scan blocked because Odoo contact "
            "must remain disabled.",
        )
        return redirect("bridge_core:session_list")

    if configuration.odoo_integration_enabled:
        messages.error(
            request,
            "Active RFID scan blocked because Odoo "
            "integration must remain disabled.",
        )
        return redirect("bridge_core:session_list")

    if settings.SENDER_BACKEND != "disabled":
        messages.error(
            request,
            "Active RFID scan blocked because the sender "
            "backend is not disabled.",
        )
        return redirect("bridge_core:session_list")

    before_count = session.raw_events.count()

    try:
        call_command(
            "run_active_rfid_session_scan",
            device_code=session.device.code,
            scan_seconds=form.cleaned_data["scan_seconds"],
            confirmation=form.cleaned_data["confirmation"],
            apply=True,
        )
    except CommandError as exc:
        messages.error(
            request,
            f"Active RFID scan failed safely: {exc}",
        )
        return redirect("bridge_core:session_list")
    except Exception as exc:
        messages.error(
            request,
            "Active RFID scan failed safely: "
            f"{type(exc).__name__}: {exc}",
        )
        return redirect("bridge_core:session_list")

    session.refresh_from_db()
    after_count = session.raw_events.count()
    created_count = max(after_count - before_count, 0)

    messages.success(
        request,
        "Active RFID scan completed. "
        f"{created_count} new unique RFID event(s) were "
        "stored and attached to session "
        f"{session.external_session_key}. "
        "No Odoo delivery was performed.",
    )

    return redirect("bridge_core:session_list")


@login_required
def event_list(request):
    events = (
        RawRFIDEvent.objects
        .select_related("device", "rfid_session")
        .all()[:200]
    )

    return render(
        request,
        "bridge_core/event_list.html",
        {
            "events": events,
        },
    )


@login_required
def delivery_attempt_list(request):
    attempts = (
        DeliveryAttempt.objects
        .select_related("event", "event__device")
        .order_by("-attempted_at", "-id")[:200]
    )

    return render(
        request,
        "bridge_core/delivery_attempt_list.html",
        {
            "attempts": attempts,
        },
    )
