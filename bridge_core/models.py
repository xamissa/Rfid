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


class RFIDObservation(models.Model):
    """Durable open aggregate; capture fields freeze on finalization."""
    event_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    device = models.ForeignKey(ReaderDevice, on_delete=models.PROTECT)
    epc = models.CharField(max_length=510)
    antenna = models.PositiveSmallIntegerField(null=True)
    # -1 represents unknown only in the uniqueness key; wire antenna stays null.
    antenna_key = models.SmallIntegerField(default=-1)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    raw_read_count = models.PositiveBigIntegerField(default=1)
    status = models.CharField(max_length=16, default='open', choices=[('open', 'Open'), ('finalized', 'Finalized')])
    finalized_at = models.DateTimeField(null=True)
    finalization_reason = models.CharField(max_length=32, blank=True)
    context_kind = models.CharField(max_length=16, choices=[(v, v) for v in ('receipt', 'delivery', 'no_transfer', 'unresolved')])
    session_key = models.CharField(max_length=128, null=True)
    operation = models.CharField(max_length=16, null=True)
    context_generation_uuid = models.UUIDField()
    connection_epoch_uuid = models.UUIDField()
    capture_run_uuid = models.UUIDField()
    context_reason = models.CharField(max_length=64)
    related_session_key = models.CharField(max_length=128, null=True)
    command_revision = models.PositiveBigIntegerField(null=True)
    diagnostic_metadata = models.JSONField(default=dict)
    frozen_payload = models.JSONField(null=True)
    payload_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=('device', 'epc', 'antenna_key', 'context_generation_uuid', 'connection_epoch_uuid'),
            condition=models.Q(status='open'), name='unique_open_observation_key'),
            models.CheckConstraint(condition=models.Q(raw_read_count__gte=1), name='obs_positive_reports'),
            models.CheckConstraint(condition=(models.Q(antenna__isnull=True, antenna_key=-1) |
                models.Q(antenna__isnull=False, antenna__lte=255, antenna_key=models.F('antenna'))),
                name='obs_antenna_key_consistent'),
            models.CheckConstraint(condition=(models.Q(status='open', finalized_at__isnull=True, frozen_payload__isnull=True) |
                models.Q(status='finalized', finalized_at__isnull=False, frozen_payload__isnull=False)),
                name='obs_finalization_consistent')]
        indexes = [models.Index(fields=('device', 'status'), name='obs_device_status_idx'),
                   models.Index(fields=('epc', 'first_seen_at'), name='obs_epc_seen_idx'),
                   models.Index(fields=('session_key',), name='obs_session_idx')]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status='finalized').exists():
            raise ValueError('Finalized observation is immutable.')
        super().save(*args, **kwargs)


class ObservationOutbox(models.Model):
    observation = models.OneToOneField(RFIDObservation, on_delete=models.PROTECT)
    state = models.CharField(max_length=16, default='pending', choices=[(v, v) for v in ('pending', 'leased', 'delivered', 'quarantined')])
    attempt_count = models.PositiveBigIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    lease_token = models.UUIDField(null=True)
    lease_owner = models.CharField(max_length=128, blank=True)
    lease_expires_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=256, blank=True)
    acknowledged_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=('state', 'next_attempt_at'), name='obs_outbox_due_idx'),
                   models.Index(fields=('state', 'lease_expires_at'), name='obs_outbox_lease_idx')]


class ObservationDeliveryAttempt(models.Model):
    outbox = models.ForeignKey(ObservationOutbox, on_delete=models.PROTECT)
    batch_uuid = models.UUIDField()
    lease_token = models.UUIDField()
    attempt_number = models.PositiveBigIntegerField()
    outcome = models.CharField(max_length=32, default='started')
    error_kind = models.CharField(max_length=128, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('outbox', 'attempt_number'), name='unique_obs_attempt')]
