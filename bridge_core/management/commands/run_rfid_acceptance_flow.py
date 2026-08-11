from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bridge_core.models import (
    RFIDSession,
    RawRFIDEvent,
    ReaderDevice,
)
from bridge_core.reader_backends import (
    FakeReaderBackend,
    TechnicalRFIDRead,
)
from bridge_core.worker_cycle import run_device_ingestion_cycle


class Command(BaseCommand):
    help = (
        "Run a controlled RFID bridge acceptance flow using the "
        "offline fake reader backend."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--device-code",
            default="receiving-door-01",
        )
        parser.add_argument(
            "--event-key",
            default="acceptance-flow-tag-001",
        )
        parser.add_argument(
            "--epc",
            default="TEST-TAG-POC-001",
        )

    def handle(self, *args, **options):
        self._validate_safety()

        device = ReaderDevice.objects.get(
            code=options["device_code"],
            enabled=True,
        )

        try:
            session = RFIDSession.objects.get(
                device=device,
                status=RFIDSession.Status.ACTIVE,
            )
        except RFIDSession.DoesNotExist as exc:
            raise CommandError(
                "No active RFID session exists for device."
            ) from exc

        read = TechnicalRFIDRead(
            reader_event_key=options["event_key"],
            epc=options["epc"],
            raw_payload=(
                f"FAKE|{options['epc']}|ACCEPTANCE"
            ),
        )

        backend = FakeReaderBackend(
            reads=(read,),
        )

        result = run_device_ingestion_cycle(
            devices=(device,),
            reader_backend=backend,
        )

        event = RawRFIDEvent.objects.get(
            reader_event_key=options["event_key"],
        )

        self.stdout.write(
            f"SESSION={session.external_session_key}"
        )
        self.stdout.write(
            f"CREATED_EVENTS={result.created_count}"
        )
        self.stdout.write(
            f"ASSIGNED_EVENTS={result.assigned_count}"
        )
        self.stdout.write(
            f"QUEUE_STATE={event.queue_state}"
        )
        self.stdout.write(
            f"SESSION_ATTACHED={event.rfid_session_id is not None}"
        )
        self.stdout.write(
            f"ODOO_CONTACT={settings.ALLOW_ODOO_CONTACT}"
        )

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: RFID acceptance flow completed safely"
            )
        )

    @staticmethod
    def _validate_safety():
        if settings.READER_BACKEND != "fake":
            raise CommandError(
                "Acceptance flow requires fake reader backend."
            )

        if settings.SENDER_BACKEND != "disabled":
            raise CommandError(
                "Acceptance flow requires disabled sender."
            )

        if settings.ALLOW_PHYSICAL_READER_CONTACT:
            raise CommandError(
                "Physical reader contact must remain disabled."
            )

        if settings.ALLOW_ODOO_CONTACT:
            raise CommandError(
                "Odoo contact must remain disabled."
            )
