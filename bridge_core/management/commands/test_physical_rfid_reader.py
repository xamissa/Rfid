import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bridge_core.models import ReaderDevice
from bridge_core.reader_backends import get_reader_backend


CONFIRMATION_PHRASE = "CONTACT_THIS_READER_ONCE"


class Command(BaseCommand):
    help = (
        "Run one explicitly approved physical RFID reader scan. "
        "The command prints returned reads and performs no database ingestion."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--device-code",
            required=True,
            help="Code of one existing enabled ReaderDevice.",
        )
        parser.add_argument(
            "--scan-seconds",
            type=float,
            default=3.0,
            help="Cached inventory scan duration from 0 to 300 seconds.",
        )
        parser.add_argument(
            "--confirm-physical-contact",
            required=True,
            help=(
                "Must exactly equal "
                f"{CONFIRMATION_PHRASE}."
            ),
        )

    def handle(self, *args, **options):
        del args

        self._validate_safety(
            confirmation=options[
                "confirm_physical_contact"
            ]
        )

        device_code = options["device_code"].strip()
        scan_seconds = options["scan_seconds"]

        if not device_code:
            raise CommandError(
                "--device-code cannot be empty."
            )

        if not 0 <= scan_seconds <= 300:
            raise CommandError(
                "--scan-seconds must be between 0 and 300."
            )

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

        if device.inventory_mode != ReaderDevice.InventoryMode.CACHED:
            raise CommandError(
                "One-shot physical testing currently supports "
                "cached inventory mode only."
            )

        self.stdout.write("MODE=one-shot-physical-reader-test")
        self.stdout.write(f"DEVICE_CODE={device.code}")
        self.stdout.write(f"DEVICE_ROLE={device.role}")
        self.stdout.write(f"READER_HOST={device.host}")
        self.stdout.write(f"READER_PORT={device.port}")
        self.stdout.write(
            f"READER_ADDRESS={device.device_address}"
        )
        self.stdout.write(
            f"INVENTORY_MODE={device.inventory_mode}"
        )
        self.stdout.write(f"SCAN_SECONDS={scan_seconds}")
        self.stdout.write("DATABASE_INGESTION=disabled")
        self.stdout.write("ODOO_CONTACT=disabled")
        self.stdout.write("WORKER_INVOCATION=disabled")

        backend = get_reader_backend(
            "cached_tcp",
            allow_physical_contact=True,
            scan_seconds=scan_seconds,
        )

        try:
            technical_reads = tuple(
                backend.read_events(device=device)
            )
        except Exception as exc:
            raise CommandError(
                f"Physical RFID test failed: {exc}"
            ) from exc

        self.stdout.write(
            f"TECHNICAL_READ_COUNT={len(technical_reads)}"
        )

        for index, technical_read in enumerate(
            technical_reads,
            start=1,
        ):
            self.stdout.write(
                f"READ_{index}_EVENT_KEY="
                f"{technical_read.reader_event_key}"
            )
            self.stdout.write(
                f"READ_{index}_EPC={technical_read.epc}"
            )

            try:
                parsed_payload = json.loads(
                    technical_read.raw_payload
                )
                normalized_payload = json.dumps(
                    parsed_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                normalized_payload = (
                    technical_read.raw_payload
                )

            self.stdout.write(
                f"READ_{index}_RAW_PAYLOAD="
                f"{normalized_payload}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: One-shot physical RFID reader test completed"
            )
        )

    @staticmethod
    def _validate_safety(*, confirmation):
        if not settings.ALLOW_PHYSICAL_READER_CONTACT:
            raise CommandError(
                "Physical reader contact is disabled by configuration."
            )

        if settings.ALLOW_ODOO_CONTACT:
            raise CommandError(
                "Odoo contact must remain disabled during "
                "physical reader testing."
            )

        if settings.SENDER_BACKEND != "disabled":
            raise CommandError(
                "Sender backend must remain disabled during "
                "physical reader testing."
            )

        if confirmation != CONFIRMATION_PHRASE:
            raise CommandError(
                "Exact physical-contact confirmation phrase required: "
                f"{CONFIRMATION_PHRASE}"
            )
