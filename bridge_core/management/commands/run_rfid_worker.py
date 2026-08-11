import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bridge_core.delivery_cycle import run_batch_delivery_cycle
from bridge_core.models import OperationalConfiguration, ReaderDevice
from bridge_core.reader_backends import get_reader_backend
from bridge_core.sender_backends import get_sender_backend
from bridge_core.worker_cycle import run_device_ingestion_cycle


class Command(BaseCommand):
    help = (
        "Run the RFID worker in offline fail-closed mode. "
        "The worker uses the configured reader and sender backends, "
        "ingests technical reads, and assigns newly created events to "
        "compatible active sessions. Physical-reader and Odoo contact "
        "remain prohibited by fail-closed settings."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Perform one safe validation cycle and exit.",
        )
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=5.0,
            help="Seconds to wait between validation cycles.",
        )
        parser.add_argument(
            "--max-cycles",
            type=int,
            default=None,
            help=(
                "Stop after this many cycles. Intended for controlled "
                "validation; omit for continuous operation."
            ),
        )
        parser.add_argument(
            "--process-delivery",
            action="store_true",
            help=(
                "Process eligible delivery events using the configured "
                "fail-closed sender backend."
            ),
        )

    def handle(self, *args, **options):
        poll_seconds = options["poll_seconds"]
        max_cycles = options["max_cycles"]
        process_delivery = options["process_delivery"]

        if poll_seconds < 0:
            raise CommandError("--poll-seconds cannot be negative.")

        if max_cycles is not None and max_cycles < 1:
            raise CommandError("--max-cycles must be at least 1.")

        if options["once"] and max_cycles is not None:
            raise CommandError(
                "--once and --max-cycles cannot be used together."
            )

        if options["once"]:
            max_cycles = 1

        self.stdout.write("RFID_WORKER_MODE=offline_validation")
        self.stdout.write(f"POLL_SECONDS={poll_seconds}")
        self.stdout.write(
            "MAX_CYCLES="
            f"{max_cycles if max_cycles is not None else 'continuous'}"
        )

        cycle_number = 0

        try:
            while max_cycles is None or cycle_number < max_cycles:
                cycle_number += 1
                self._validate_offline_cycle(
                    cycle_number,
                    process_delivery=process_delivery,
                )

                if max_cycles is not None and cycle_number >= max_cycles:
                    break

                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "HOLD: RFID worker stopped by operator"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"PASS: RFID worker completed {cycle_number} safe cycle(s)"
            )
        )

    def _validate_offline_cycle(
        self,
        cycle_number,
        *,
        process_delivery=False,
    ):
        configuration = OperationalConfiguration.objects.get(name="default")

        if settings.READER_BACKEND != "fake":
            raise CommandError(
                "Unsafe reader backend detected during offline validation."
            )

        if settings.SENDER_BACKEND != "disabled":
            raise CommandError(
                "Unsafe sender backend detected during offline validation."
            )

        if settings.ALLOW_PHYSICAL_READER_CONTACT:
            raise CommandError(
                "Physical reader contact must remain disabled."
            )

        if settings.ALLOW_ODOO_CONTACT:
            raise CommandError(
                "Odoo contact must remain disabled."
            )

        reader_backend = get_reader_backend(settings.READER_BACKEND)
        sender_backend = get_sender_backend(
            settings.SENDER_BACKEND,
            configuration=configuration,
        )
        enabled_devices = tuple(
            ReaderDevice.objects.filter(enabled=True).order_by("code")
        )
        cycle_result = run_device_ingestion_cycle(
            devices=enabled_devices,
            reader_backend=reader_backend,
        )

        delivery_result = None

        if process_delivery:
            delivery_result = run_batch_delivery_cycle(
                sender_backend=sender_backend,
                batch_size=configuration.worker_batch_size,
                max_delivery_attempts=(
                    configuration.max_delivery_attempts
                ),
                retry_initial_seconds=(
                    configuration.retry_initial_seconds
                ),
                retry_max_seconds=configuration.retry_max_seconds,
            )

        self.stdout.write(f"CYCLE={cycle_number}")
        self.stdout.write(f"READER_BACKEND={settings.READER_BACKEND}")
        self.stdout.write(f"SENDER_BACKEND={settings.SENDER_BACKEND}")
        self.stdout.write(
            "ALLOW_PHYSICAL_READER_CONTACT="
            f"{settings.ALLOW_PHYSICAL_READER_CONTACT}"
        )
        self.stdout.write(
            f"ALLOW_ODOO_CONTACT={settings.ALLOW_ODOO_CONTACT}"
        )
        self.stdout.write(
            f"WORKER_BATCH_SIZE={configuration.worker_batch_size}"
        )
        self.stdout.write(
            "MAX_DELIVERY_ATTEMPTS="
            f"{configuration.max_delivery_attempts}"
        )
        self.stdout.write(
            f"ENABLED_READER_DEVICES={cycle_result.device_count}"
        )
        self.stdout.write(
            f"TECHNICAL_READS={cycle_result.received_count}"
        )
        self.stdout.write(
            f"CREATED_EVENTS={cycle_result.created_count}"
        )
        self.stdout.write(
            f"DUPLICATE_EVENTS={cycle_result.duplicate_count}"
        )
        self.stdout.write(
            "ASSIGNMENT_SELECTED="
            f"{cycle_result.assignment_selected_count}"
        )
        self.stdout.write(
            f"ASSIGNED_EVENTS={cycle_result.assigned_count}"
        )
        self.stdout.write(
            f"UNASSIGNED_EVENTS={cycle_result.unassigned_count}"
        )
        self.stdout.write(
            "ASSIGNMENT_FAILED="
            f"{cycle_result.assignment_failed_count}"
        )
        self.stdout.write(
            "DELIVERY_PROCESSING="
            f"{'enabled' if process_delivery else 'disabled'}"
        )

        if delivery_result is None:
            self.stdout.write(
                "HOLD: Delivery processing requires "
                "--process-delivery"
            )
        else:
            self.stdout.write(
                f"DELIVERY_SELECTED={delivery_result.selected_count}"
            )
            self.stdout.write(
                f"DELIVERY_PROCESSED={delivery_result.processed_count}"
            )
            self.stdout.write(
                f"DELIVERY_SENT={delivery_result.sent_count}"
            )
            self.stdout.write(
                f"DELIVERY_RETRY={delivery_result.retry_count}"
            )
            self.stdout.write(
                f"DELIVERY_REJECTED={delivery_result.rejected_count}"
            )
            self.stdout.write(
                f"DELIVERY_DEAD={delivery_result.dead_count}"
            )
            self.stdout.write(
                f"DELIVERY_FAILED={delivery_result.failed_count}"
            )

        self.stdout.write(
            "HOLD: Odoo contact remains disabled"
        )
