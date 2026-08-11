from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bridge_core.ingestion import ingest_technical_reads
from bridge_core.models import (
    OperationalConfiguration,
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)
from bridge_core.reader_backends import get_reader_backend
from bridge_core.session_assignment_cycle import (
    run_active_session_assignment_cycle,
)


CONFIRMATION_PHRASE = "SCAN_AND_STORE_ACTIVE_SESSION"


class Command(BaseCommand):
    help = (
        "Run one controlled active RFID scan, store unique EPC events, "
        "and attach them to the active session for the selected reader. "
        "Odoo delivery is not performed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--device-code",
            required=True,
            help="Existing enabled reader device code.",
        )
        parser.add_argument(
            "--scan-seconds",
            type=float,
            default=10.0,
            help="Active inventory duration from greater than 0 to 600 seconds.",
        )
        parser.add_argument(
            "--confirmation",
            default="",
            help=(
                "Required with --apply. Enter "
                "SCAN_AND_STORE_ACTIVE_SESSION exactly."
            ),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Contact the reader and persist events. "
                "Without this flag, validation is dry-run only."
            ),
        )

    def handle(self, *args, **options):
        device_code = options["device_code"].strip()
        scan_seconds = options["scan_seconds"]
        confirmation = options["confirmation"].strip()
        apply_changes = options["apply"]

        if not device_code:
            raise CommandError("--device-code cannot be empty.")

        if not 0 < scan_seconds <= 600:
            raise CommandError(
                "--scan-seconds must be greater than 0 "
                "and no more than 600."
            )

        try:
            configuration = OperationalConfiguration.objects.get(
                name="default"
            )
        except OperationalConfiguration.DoesNotExist as exc:
            raise CommandError(
                "Default operational configuration does not exist."
            ) from exc

        try:
            reader = ReaderDevice.objects.get(
                code=device_code,
                enabled=True,
            )
        except ReaderDevice.DoesNotExist as exc:
            raise CommandError(
                f"Enabled reader device not found: {device_code}"
            ) from exc

        try:
            active_session = RFIDSession.objects.get(
                device=reader,
                status=RFIDSession.Status.ACTIVE,
            )
        except RFIDSession.DoesNotExist as exc:
            raise CommandError(
                "No active RFID session exists for this reader. "
                "The reader was not contacted."
            ) from exc

        self._validate_session_compatibility(
            reader=reader,
            session=active_session,
        )

        self.stdout.write(
            f"MODE={'apply' if apply_changes else 'dry-run'}"
        )
        self.stdout.write(f"DEVICE_CODE={reader.code}")
        self.stdout.write(f"DEVICE_ROLE={reader.role}")
        self.stdout.write(
            f"DATABASE_INVENTORY_MODE={reader.inventory_mode}"
        )
        self.stdout.write(
            f"ACTIVE_SESSION={active_session.external_session_key}"
        )
        self.stdout.write(
            f"SESSION_OPERATION={active_session.operation_type}"
        )
        self.stdout.write(
            f"ODOO_REFERENCE={active_session.odoo_reference}"
        )
        self.stdout.write(f"SCAN_SECONDS={scan_seconds}")
        self.stdout.write(
            "ODOO_INTEGRATION_ENABLED="
            f"{configuration.odoo_integration_enabled}"
        )
        self.stdout.write(
            "ODOO_CONTACT_ALLOWED="
            f"{configuration.poc_allow_odoo_contact}"
        )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "HOLD: Dry-run complete; the reader was not contacted "
                    "and no events were stored"
                )
            )
            return

        if confirmation != CONFIRMATION_PHRASE:
            raise CommandError(
                "Enter the exact confirmation phrase "
                "SCAN_AND_STORE_ACTIVE_SESSION."
            )

        self._validate_contact_safety(configuration)

        active_device = SimpleNamespace(
            code=reader.code,
            enabled=reader.enabled,
            inventory_mode=ReaderDevice.InventoryMode.ACTIVE,
            host=reader.host,
            port=reader.port,
            device_address=reader.device_address,
            connect_timeout_seconds=reader.connect_timeout_seconds,
            read_timeout_seconds=reader.read_timeout_seconds,
        )

        before_event_count = RawRFIDEvent.objects.count()

        backend = get_reader_backend(
            "active_tcp",
            allow_physical_contact=True,
            scan_seconds=scan_seconds,
        )
        technical_reads = tuple(
            backend.read_events(device=active_device)
        )

        existing_session_epcs = {
            epc.strip().upper()
            for epc in (
                active_session.raw_events
                .exclude(epc="")
                .values_list("epc", flat=True)
            )
            if epc and epc.strip()
        }

        new_technical_reads = tuple(
            technical_read
            for technical_read in technical_reads
            if technical_read.epc.strip().upper()
            not in existing_session_epcs
        )

        existing_session_duplicate_count = (
            len(technical_reads) - len(new_technical_reads)
        )

        ingestion_result = ingest_technical_reads(
            device=reader,
            technical_reads=new_technical_reads,
        )
        assignment_result = run_active_session_assignment_cycle(
            event_ids=ingestion_result.created_event_ids,
        )

        after_event_count = RawRFIDEvent.objects.count()

        self.stdout.write(
            f"TECHNICAL_READS={len(technical_reads)}"
        )
        self.stdout.write(
            "EXISTING_SESSION_EPCS="
            f"{len(existing_session_epcs)}"
        )
        self.stdout.write(
            "ALREADY_IN_SESSION="
            f"{existing_session_duplicate_count}"
        )
        self.stdout.write(
            "NEW_TECHNICAL_READS="
            f"{len(new_technical_reads)}"
        )
        self.stdout.write(
            f"CREATED_EVENTS={ingestion_result.created_count}"
        )
        self.stdout.write(
            f"DUPLICATE_EVENTS={ingestion_result.duplicate_count}"
        )
        self.stdout.write(
            f"ASSIGNMENT_SELECTED={assignment_result.selected_count}"
        )
        self.stdout.write(
            f"ASSIGNED_EVENTS={assignment_result.assigned_count}"
        )
        self.stdout.write(
            f"UNASSIGNED_EVENTS={assignment_result.unassigned_count}"
        )
        self.stdout.write(
            f"ASSIGNMENT_FAILED={assignment_result.failed_count}"
        )
        self.stdout.write(
            f"DATABASE_EVENT_COUNT_BEFORE={before_event_count}"
        )
        self.stdout.write(
            f"DATABASE_EVENT_COUNT_AFTER={after_event_count}"
        )
        self.stdout.write("ODOO_CONTACT=NO")
        self.stdout.write("DELIVERY_PROCESSING=NO")

        if assignment_result.failed_count:
            raise CommandError(
                "One or more created events failed session assignment."
            )

        if assignment_result.unassigned_count:
            raise CommandError(
                "One or more created events remained unassigned."
            )

        if assignment_result.assigned_count != ingestion_result.created_count:
            raise CommandError(
                "Created and assigned event counts do not match."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: Active RFID scan stored and attached "
                "to the active session"
            )
        )

    @staticmethod
    def _validate_session_compatibility(*, reader, session):
        expected_operation = {
            ReaderDevice.Role.RECEIVING: (
                RFIDSession.OperationType.RECEIPT
            ),
            ReaderDevice.Role.DISPATCH: (
                RFIDSession.OperationType.DISPATCH
            ),
        }.get(reader.role)

        if expected_operation is None:
            raise CommandError(
                "Reader has an unsupported operational role."
            )

        if session.operation_type != expected_operation:
            raise CommandError(
                "The active session operation is incompatible "
                "with the reader role."
            )

    @staticmethod
    def _validate_contact_safety(configuration):
        reader_contact_allowed = bool(
            configuration.poc_allow_physical_reader_contact
            or settings.ALLOW_PHYSICAL_READER_CONTACT
        )

        if not reader_contact_allowed:
            raise CommandError(
                "Physical-reader contact is disabled."
            )

        if (
            configuration.poc_allow_odoo_contact
            or settings.ALLOW_ODOO_CONTACT
        ):
            raise CommandError(
                "Odoo contact must remain disabled during "
                "the active session scan."
            )

        if configuration.odoo_integration_enabled:
            raise CommandError(
                "Odoo integration must remain disabled during "
                "the active session scan."
            )

        if settings.SENDER_BACKEND != "disabled":
            raise CommandError(
                "The sender backend must remain disabled."
            )
