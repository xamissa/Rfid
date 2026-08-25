import time

from django.conf import settings
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from bridge_core.final_delivery_cycle import (
    run_final_delivery_cycle,
)
from bridge_core.final_odoo_event_sender import (
    FinalOdooEventSender,
)
from bridge_core.final_runtime_config import (
    load_final_runtime_configuration,
)
from bridge_core.models import (
    OperationalConfiguration,
)
from bridge_core.odoo_api_v1 import (
    OdooRFIDApiClient,
)


class Command(BaseCommand):
    help = (
        "Run the final RFID Odoo event-delivery worker. "
        "This process is independent from physical reader capture "
        "and control polling."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reader-code",
            default=None,
            help=(
                "Override RFID_READER_CODE for this delivery worker "
                "instance. If omitted, the configured "
                "RFID_READER_CODE is used."
            ),
        )

        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one delivery cycle and exit.",
        )

        parser.add_argument(
            "--max-cycles",
            type=int,
            default=None,
            help="Run a finite number of delivery cycles.",
        )

        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=1.0,
            help="Seconds between delivery cycles.",
        )

    def handle(self, *args, **options):
        if (
            options["once"]
            and options["max_cycles"] is not None
        ):
            raise CommandError(
                "--once and --max-cycles cannot be used together."
            )

        max_cycles = (
            1
            if options["once"]
            else options["max_cycles"]
        )

        if max_cycles is not None and max_cycles < 1:
            raise CommandError(
                "--max-cycles must be at least 1."
            )

        poll_seconds = float(
            options["poll_seconds"]
        )

        if not 0.1 <= poll_seconds <= 60:
            raise CommandError(
                "--poll-seconds must be between 0.1 and 60."
            )

        self._require_live_gate()

        final_configuration = (
            load_final_runtime_configuration(
                settings.CONFIGURATION
            )
        )

        reader_code = str(
            options.get("reader_code")
            or final_configuration.reader_code
        ).strip()

        if not reader_code:
            raise CommandError(
                "Final RFID reader code cannot be empty."
            )

        operational = (
            OperationalConfiguration.objects.get(
                name="default"
            )
        )

        api_client = OdooRFIDApiClient(
            base_url=(
                final_configuration.odoo_base_url
            ),
            bearer_token=(
                final_configuration.bearer_token
            ),
            gateway_code=(
                final_configuration.gateway_code
            ),
            timeout_seconds=(
                final_configuration
                .request_timeout_seconds
            ),
            verify_tls=(
                final_configuration.verify_tls
            ),
        )

        sender = FinalOdooEventSender(
            api_client=api_client,
            reader_code=reader_code,
        )

        self.stdout.write(
            "RFID_FINAL_DELIVERY_WORKER=starting"
        )

        self.stdout.write(
            "RFID_READER_CODE="
            f"{reader_code}"
        )

        self.stdout.write(
            "WORKER_BATCH_SIZE="
            f"{operational.worker_batch_size}"
        )

        self.stdout.write(
            "MAX_DELIVERY_ATTEMPTS="
            f"{operational.max_delivery_attempts}"
        )

        self.stdout.write(
            "RETRY_INITIAL_SECONDS="
            f"{operational.retry_initial_seconds}"
        )

        self.stdout.write(
            "RETRY_MAX_SECONDS="
            f"{operational.retry_max_seconds}"
        )

        self.stdout.write(
            f"POLL_SECONDS={poll_seconds}"
        )

        cycle_number = 0

        try:
            while (
                max_cycles is None
                or cycle_number < max_cycles
            ):
                cycle_number += 1

                result = run_final_delivery_cycle(
                    sender=sender,
                    reader_code=reader_code,
                    batch_size=(
                        operational.worker_batch_size
                    ),
                    max_delivery_attempts=(
                        operational.max_delivery_attempts
                    ),
                    retry_initial_seconds=(
                        operational.retry_initial_seconds
                    ),
                    retry_max_seconds=(
                        operational.retry_max_seconds
                    ),
                )

                self.stdout.write(
                    "CYCLE="
                    f"{cycle_number} "
                    "SELECTED="
                    f"{result.selected_count} "
                    "PROCESSED="
                    f"{result.processed_count} "
                    "SENT="
                    f"{result.sent_count} "
                    "RETRY="
                    f"{result.retry_count} "
                    "REJECTED="
                    f"{result.rejected_count} "
                    "DEAD="
                    f"{result.dead_count} "
                    "EXHAUSTED_DEAD="
                    f"{result.exhausted_dead_count} "
                    "FAILED="
                    f"{result.failed_count}"
                )

                if (
                    max_cycles is not None
                    and cycle_number >= max_cycles
                ):
                    break

                time.sleep(
                    poll_seconds
                )

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "HOLD: final RFID delivery worker "
                    "stopped by operator"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: final RFID delivery worker completed "
                f"{cycle_number} cycle(s)"
            )
        )

    @staticmethod
    def _require_live_gate():
        if not settings.ALLOW_ODOO_CONTACT:
            raise CommandError(
                "Final RFID delivery worker requires "
                "ALLOW_ODOO_CONTACT=True."
            )

        if settings.SENDER_BACKEND != "disabled":
            raise CommandError(
                "Final RFID delivery worker requires "
                "SENDER_BACKEND=disabled so the legacy "
                "delivery path remains inactive."
            )
