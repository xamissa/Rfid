from django.core.management.base import BaseCommand, CommandError

from bridge_core.models import ReaderDevice, RFIDSession


class Command(BaseCommand):
    help = (
        "Create one explicit POC RFID session. "
        "Used to simulate the future Odoo-created session contract."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--device-code",
            required=True,
            help="Existing enabled reader device code.",
        )
        parser.add_argument(
            "--session-key",
            required=True,
            help="External session identifier supplied by the controlling system.",
        )
        parser.add_argument(
            "--odoo-record-id",
            required=True,
            type=int,
            help="Simulated Odoo record ID.",
        )
        parser.add_argument(
            "--odoo-reference",
            required=True,
            help="Simulated Odoo document reference.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the session. Without this flag, dry-run only.",
        )

    def handle(self, *args, **options):
        device_code = options["device_code"].strip()
        session_key = options["session_key"].strip()
        odoo_record_id = options["odoo_record_id"]
        odoo_reference = options["odoo_reference"].strip()

        if not device_code:
            raise CommandError("--device-code cannot be empty.")

        if not session_key:
            raise CommandError("--session-key cannot be empty.")

        try:
            device = ReaderDevice.objects.get(
                code=device_code,
                enabled=True,
            )
        except ReaderDevice.DoesNotExist as exc:
            raise CommandError(
                f"Enabled reader device not found: {device_code}"
            ) from exc

        operation_type = self._operation_from_device(device)

        self.stdout.write(
            "MODE="
            + ("apply" if options["apply"] else "dry-run")
        )
        self.stdout.write(f"DEVICE_CODE={device.code}")
        self.stdout.write(f"DEVICE_ROLE={device.role}")
        self.stdout.write(f"OPERATION={operation_type}")
        self.stdout.write("ODOO_MODEL=stock.picking")
        self.stdout.write(f"ODOO_RECORD_ID={odoo_record_id}")
        self.stdout.write(f"ODOO_REFERENCE={odoo_reference}")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "HOLD: Dry-run complete; no session was created"
                )
            )
            return

        session = RFIDSession.objects.create(
            external_session_key=session_key,
            device=device,
            operation_type=operation_type,
            odoo_model="stock.picking",
            odoo_record_id=odoo_record_id,
            odoo_reference=odoo_reference,
            status=RFIDSession.Status.ACTIVE,
        )

        self.stdout.write(f"SESSION_ID={session.session_id}")

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: POC RFID session created safely"
            )
        )

    @staticmethod
    def _operation_from_device(device):
        if device.role == ReaderDevice.Role.RECEIVING:
            return RFIDSession.OperationType.RECEIPT

        if device.role == ReaderDevice.Role.DISPATCH:
            return RFIDSession.OperationType.DISPATCH

        raise CommandError(
            "Unsupported reader role for RFID session creation."
        )
