import uuid

from django.db import models


class ReaderDevice(models.Model):
    class Role(models.TextChoices):
        RECEIVING = "receiving", "Receiving"
        DISPATCH = "dispatch", "Dispatch"

    class InventoryMode(models.TextChoices):
        CACHED = "cached", "Cached inventory"
        ACTIVE = "active", "Active reporting"

    code = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Stable internal identifier for this reader device.",
    )
    name = models.CharField(max_length=128)
    role = models.CharField(max_length=16, choices=Role.choices)
    host = models.CharField(
        max_length=255,
        blank=True,
        help_text="Reader hostname or IP address.",
    )
    port = models.PositiveIntegerField(
        default=8090,
        help_text="Reader TCP port.",
    )
    device_address = models.PositiveSmallIntegerField(
        default=1,
        help_text="Reader protocol device address.",
    )
    inventory_mode = models.CharField(
        max_length=16,
        choices=InventoryMode.choices,
        default=InventoryMode.CACHED,
    )
    connect_timeout_seconds = models.PositiveSmallIntegerField(default=5)
    read_timeout_seconds = models.PositiveSmallIntegerField(default=5)
    reconnect_delay_seconds = models.PositiveSmallIntegerField(default=5)
    enabled = models.BooleanField(default=False)
    shared_operations = models.BooleanField(
        default=False,
        help_text=(
            "Allow this physical reader to serve both Receiving and "
            "Dispatch sessions. Keep disabled for dedicated readers."
        ),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("code",)

    def __str__(self) -> str:
        return f"{self.code} ({self.get_role_display()})"


class RFIDSession(models.Model):
    class OperationType(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        DISPATCH = "dispatch", "Dispatch"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    session_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    external_session_key = models.CharField(
        max_length=128,
        unique=True,
        help_text=(
            "Opaque session identifier supplied by the controlling system."
        ),
    )
    device = models.ForeignKey(
        ReaderDevice,
        on_delete=models.PROTECT,
        related_name="rfid_sessions",
    )
    operation_type = models.CharField(
        max_length=16,
        choices=OperationType.choices,
    )
    odoo_model = models.CharField(
        max_length=64,
        default="stock.picking",
    )
    odoo_record_id = models.PositiveBigIntegerField()
    odoo_reference = models.CharField(
        max_length=128,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-opened_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("device",),
                condition=models.Q(status="active"),
                name="unique_active_rfid_session_per_device",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "opened_at"),
                name="rfid_session_status_opened_idx",
            ),
            models.Index(
                fields=("odoo_model", "odoo_record_id"),
                name="rfid_session_odoo_record_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.external_session_key}:"
            f"{self.device.code}:"
            f"{self.status}"
        )


class RawRFIDEvent(models.Model):
    class QueueState(models.TextChoices):
        UNASSIGNED = "unassigned", "Unassigned"
        QUEUED = "queued", "Queued"
        INFLIGHT = "inflight", "Inflight"
        RETRY = "retry", "Retry"
        SENT = "sent", "Sent"
        REJECTED = "rejected", "Rejected"
        DEAD = "dead", "Dead"

    event_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    device = models.ForeignKey(
        ReaderDevice,
        on_delete=models.PROTECT,
        related_name="raw_events",
    )
    rfid_session = models.ForeignKey(
        RFIDSession,
        on_delete=models.PROTECT,
        related_name="raw_events",
        null=True,
        blank=True,
    )
    reader_event_key = models.CharField(max_length=128)
    epc = models.CharField(max_length=128, blank=True)
    raw_payload = models.TextField()
    queue_state = models.CharField(
        max_length=16,
        choices=QueueState.choices,
        default=QueueState.UNASSIGNED,
    )
    received_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("received_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("device", "reader_event_key"),
                name="unique_reader_event_key_per_device",
            ),
        ]
        indexes = [
            models.Index(
                fields=("queue_state", "received_at"),
                name="rfid_event_queue_received_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.device.code}:{self.reader_event_key}"


class DeliveryAttempt(models.Model):
    class Outcome(models.TextChoices):
        STARTED = "started", "Started"
        SENT = "sent", "Sent"
        RETRY = "retry", "Retry"
        REJECTED = "rejected", "Rejected"
        DEAD = "dead", "Dead"

    event = models.ForeignKey(
        RawRFIDEvent,
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
    )
    attempt_number = models.PositiveIntegerField()
    outcome = models.CharField(
        max_length=16,
        choices=Outcome.choices,
        default=Outcome.STARTED,
    )
    response_code = models.CharField(max_length=64, blank=True)
    error_kind = models.CharField(max_length=128, blank=True)
    detail = models.TextField(blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("event_id", "attempt_number")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "attempt_number"),
                name="unique_delivery_attempt_number_per_event",
            ),
        ]
        indexes = [
            models.Index(
                fields=("outcome", "attempted_at"),
                name="delivery_outcome_attempted_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event.event_id}:attempt-{self.attempt_number}"


class OperationalConfiguration(models.Model):
    class PocReaderBackend(models.TextChoices):
        FAKE = "fake", "Fake reader"
        CACHED_TCP = "cached_tcp", "Cached TCP reader"
        ACTIVE_TCP = "active_tcp", "Active TCP reader"

    class OdooAuthenticationMethod(models.TextChoices):
        NONE = "none", "No authentication"
        BEARER_TOKEN = "bearer_token", "Bearer token"
        BASIC = "basic", "Username and secret"
        API_KEY = "api_key", "API key"
        ODOO_SESSION = (
            "odoo_session",
            "Odoo username and password session",
        )

    name = models.SlugField(
        max_length=64,
        unique=True,
        default="default",
        help_text="Stable identifier for this non-secret configuration.",
    )
    poc_reader_backend = models.CharField(
        max_length=32,
        choices=PocReaderBackend.choices,
        default=PocReaderBackend.FAKE,
        help_text=(
            "Reader backend used by controlled configuration actions."
        ),
    )
    poc_allow_physical_reader_contact = models.BooleanField(
        default=False,
        help_text=(
            "Allow controlled configuration actions to contact configured "
            "RFID readers."
        ),
    )
    poc_allow_odoo_contact = models.BooleanField(
        default=False,
        help_text=(
            "Allow controlled configuration actions to contact the "
            "configured Odoo staging database."
        ),
    )

    worker_batch_size = models.PositiveIntegerField(default=50)
    max_delivery_attempts = models.PositiveIntegerField(default=10)
    retry_initial_seconds = models.PositiveIntegerField(default=30)
    retry_max_seconds = models.PositiveIntegerField(default=3600)
    event_retention_days = models.PositiveIntegerField(default=90)

    odoo_base_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Base URL of the approved Odoo.sh environment.",
    )
    odoo_database = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Optional Odoo database or environment identifier."
        ),
    )
    odoo_session_endpoint = models.CharField(
        max_length=500,
        blank=True,
        help_text="Relative or absolute RFID session endpoint.",
    )
    odoo_event_endpoint = models.CharField(
        max_length=500,
        blank=True,
        help_text="Relative or absolute RFID event endpoint.",
    )
    odoo_authentication_method = models.CharField(
        max_length=32,
        choices=OdooAuthenticationMethod.choices,
        default=OdooAuthenticationMethod.NONE,
    )
    odoo_client_identifier = models.CharField(
        max_length=255,
        blank=True,
        help_text="Username, client ID, or integration identifier.",
    )
    odoo_secret_encrypted = models.TextField(
        blank=True,
        editable=False,
        help_text="Encrypted Odoo integration credential.",
    )
    odoo_request_timeout_seconds = models.PositiveSmallIntegerField(
        default=10,
    )
    odoo_verify_tls = models.BooleanField(default=True)
    odoo_integration_enabled = models.BooleanField(default=False)

    odoo_inventory_count_poc_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Enable the temporary inventory validation proof "
            "of concept configuration."
        ),
    )
    odoo_inventory_count_endpoint = models.CharField(
        max_length=500,
        blank=True,
        default="/api/rfid/inventory_count",
        help_text=(
            "Validation endpoint that accepts location_id "
            "and rfid_tags."
        ),
    )
    odoo_inventory_count_location_id = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text=(
            "Odoo staging stock.location record ID used only "
            "by the inventory-count POC."
        ),
    )

    setup_completed = models.BooleanField(
        default=False,
        help_text="Indicates whether the initial deployment wizard has been completed.",
    )
    setup_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the initial deployment wizard was completed.",
    )

    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Operational configuration"
        verbose_name_plural = "Operational configurations"

    def __str__(self) -> str:
        return self.name

    @property
    def has_odoo_secret(self) -> bool:
        return bool(self.odoo_secret_encrypted)

    def set_odoo_secret(self, plaintext: str) -> None:
        from bridge_core.credential_crypto import encrypt_credential

        self.odoo_secret_encrypted = encrypt_credential(plaintext)

    def get_odoo_secret(self) -> str:
        from bridge_core.credential_crypto import decrypt_credential

        return decrypt_credential(self.odoo_secret_encrypted)
