from django.urls import path

from . import views

app_name = "bridge_core"

urlpatterns = [
    path(
        "setup/",
        views.setup_wizard,
        name="setup_wizard",
    ),
    path("", views.dashboard, name="dashboard"),
    path("readers/", views.reader_list, name="reader_list"),
    path("readers/add/", views.reader_create, name="reader_create"),
    path(
        "readers/<int:reader_id>/edit/",
        views.reader_update,
        name="reader_update",
    ),
    path(
        "readers/<int:reader_id>/test/",
        views.reader_validation,
        name="reader_validation",
    ),
    path(
        "settings/poc-controls/",
        views.poc_control_centre,
        name="poc_control_centre",
    ),
    path(
        "settings/operations/",
        views.operational_settings,
        name="operational_settings",
    ),
    path(
        "settings/odoo/",
        views.odoo_integration_settings,
        name="odoo_integration_settings",
    ),
    path(
        "settings/odoo/test/",
        views.odoo_connection_test,
        name="odoo_connection_test",
    ),
    path(
        "settings/odoo/poc-inventory-test/",
        views.odoo_inventory_count_poc_manual_test,
        name="odoo_inventory_count_poc_manual_test",
    ),
    path("sessions/", views.session_list, name="session_list"),
    path(
        "sessions/<int:session_id>/active-scan/",
        views.active_session_scan,
        name="active_session_scan",
    ),
    path("events/", views.event_list, name="event_list"),
    path(
        "delivery-attempts/",
        views.delivery_attempt_list,
        name="delivery_attempt_list",
    ),
]
