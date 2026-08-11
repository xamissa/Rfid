from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bridge_core.ingestion import ingest_technical_reads
from bridge_core.models import ReaderDevice
from bridge_core.reader_backends import TechnicalRFIDRead


class Command(BaseCommand):
    help = (
        "Validate or inject one explicit fake RFID read. "
        "Dry-run is the default; --apply is required to persist an event."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--device-code",
            required=True,
            help="Code of an existing enabled ReaderDevice.",
        )
        parser.add_argument(
            "--event-key",
            required=True,
            help="Stable fake reader event identifier.",
        )
        parser.add_argument(
            "--epc",
            required=True,
            help="Fake EPC value.",
        )
        parser.add_argument(
            "--raw-payload",
            required=True,
            help="Explicit fake raw payload text.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the fake event. Without this flag, no write occurs.",
        )

    def handle(self, *args, **options):
        self._validate_offline_safety()

        device_code = options["device_code"].strip()
        event_key = options["event_key"].strip()
        epc = options["epc"].strip()
        raw_payload = options["raw_payload"]

        if not device_code:
            raise CommandError("--device-code cannot be empty.")

        if not event_key:
            raise CommandError("--event-key cannot be empty.")

        if not epc:
            raise CommandError("--epc cannot be empty.")

        try:
            device = ReaderDevice.objects.get(
                code=device_code,
                enabled=True,
            )
        except ReaderDevice.DoesNotExist as exc:
            raise CommandError(
                "Enabled reader device was not found: "
                f"{device_code}"
            ) from exc

        technical_read = TechnicalRFIDRead(
            reader_event_key=event_key,
            epc=epc,
            raw_payload=raw_payload,
        )

        self.stdout.write("MODE=" + ("apply" if options["apply"] else "dry-run"))
        self.stdout.write(f"DEVICE_CODE={device.code}")
        self.stdout.write(f"DEVICE_ROLE={device.role}")
        self.stdout.write(f"EVENT_KEY={technical_read.reader_event_key}")
        self.stdout.write(f"EPC={technical_read.epc}")
        self.stdout.write("READER_BACKEND=fake")
        self.stdout.write("SENDER_BACKEND=disabled")
        self.stdout.write("ALLOW_PHYSICAL_READER_CONTACT=False")
        self.stdout.write("ALLOW_ODOO_CONTACT=False")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "HOLD: Dry-run complete; no database event was created"
                )
            )
            return

        result = ingest_technical_reads(
            device=device,
            technical_reads=(technical_read,),
        )

        self.stdout.write(f"RECEIVED_EVENTS={result.received_count}")
        self.stdout.write(f"CREATED_EVENTS={result.created_count}")
        self.stdout.write(f"DUPLICATE_EVENTS={result.duplicate_count}")
        self.stdout.write(
            self.style.SUCCESS(
                "PASS: Explicit fake RFID read processed safely"
            )
        )

    @staticmethod
    def _validate_offline_safety():
        if settings.READER_BACKEND != "fake":
            raise CommandError(
                "Fake-read injection requires READER_BACKEND=fake."
            )

        if settings.SENDER_BACKEND != "disabled":
            raise CommandError(
                "Fake-read injection requires SENDER_BACKEND=disabled."
            )

        if settings.ALLOW_PHYSICAL_READER_CONTACT:
            raise CommandError(
                "Physical reader contact must remain disabled."
            )

        if settings.ALLOW_ODOO_CONTACT:
            raise CommandError(
                "Odoo contact must remain disabled."
            )
