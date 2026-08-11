from django.contrib import admin

from .models import (
    DeliveryAttempt,
    OperationalConfiguration,
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)


class ReadOnlyOperationalRecordAdmin(admin.ModelAdmin):
    """Allow operational records to be inspected but never edited."""

    def has_add_permission(self, request):
        del request
        return False

    def has_change_permission(self, request, obj=None):
        del request, obj
        return False

    def has_delete_permission(self, request, obj=None):
        del request, obj
        return False


@admin.register(ReaderDevice)
class ReaderDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "role",
        "host",
        "port",
        "device_address",
        "inventory_mode",
        "enabled",
        "updated_at",
    )
    list_filter = (
        "role",
        "inventory_mode",
        "enabled",
    )
    search_fields = (
        "code",
        "name",
        "host",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    ordering = ("code",)


@admin.register(RFIDSession)
class RFIDSessionAdmin(ReadOnlyOperationalRecordAdmin):
    list_display = (
        "session_id",
        "external_session_key",
        "device",
        "operation_type",
        "odoo_reference",
        "status",
        "opened_at",
        "closed_at",
    )
    list_filter = (
        "status",
        "operation_type",
        "device",
    )
    search_fields = (
        "session_id",
        "external_session_key",
        "device__code",
        "odoo_model",
        "odoo_record_id",
        "odoo_reference",
    )
    readonly_fields = (
        "id",
        "session_id",
        "external_session_key",
        "device",
        "operation_type",
        "odoo_model",
        "odoo_record_id",
        "odoo_reference",
        "status",
        "opened_at",
        "closed_at",
        "created_at",
        "updated_at",
    )
    ordering = (
        "-opened_at",
        "-id",
    )


@admin.register(RawRFIDEvent)
class RawRFIDEventAdmin(ReadOnlyOperationalRecordAdmin):
    list_display = (
        "event_id",
        "device",
        "rfid_session",
        "reader_event_key",
        "epc",
        "queue_state",
        "received_at",
    )
    list_filter = (
        "queue_state",
        "device",
    )
    search_fields = (
        "event_id",
        "reader_event_key",
        "epc",
        "device__code",
        "rfid_session__external_session_key",
    )
    readonly_fields = (
        "id",
        "event_id",
        "device",
        "rfid_session",
        "reader_event_key",
        "epc",
        "raw_payload",
        "queue_state",
        "received_at",
        "updated_at",
    )
    ordering = (
        "-received_at",
        "-id",
    )


@admin.register(DeliveryAttempt)
class DeliveryAttemptAdmin(ReadOnlyOperationalRecordAdmin):
    list_display = (
        "event",
        "attempt_number",
        "outcome",
        "response_code",
        "attempted_at",
        "completed_at",
        "next_retry_at",
    )
    list_filter = (
        "outcome",
    )
    search_fields = (
        "event__event_id",
        "event__reader_event_key",
        "response_code",
        "error_kind",
    )
    readonly_fields = (
        "id",
        "event",
        "attempt_number",
        "outcome",
        "response_code",
        "error_kind",
        "detail",
        "attempted_at",
        "completed_at",
        "next_retry_at",
    )
    ordering = (
        "-attempted_at",
        "-id",
    )


@admin.register(OperationalConfiguration)
class OperationalConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "worker_batch_size",
        "max_delivery_attempts",
        "retry_initial_seconds",
        "retry_max_seconds",
        "event_retention_days",
        "updated_at",
    )
    search_fields = ("name",)
    readonly_fields = (
        "name",
        "updated_at",
    )
    ordering = ("name",)

    def has_add_permission(self, request):
        del request
        return False

    def has_delete_permission(self, request, obj=None):
        del request, obj
        return False
