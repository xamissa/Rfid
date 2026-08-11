import time

from django.conf import settings
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from bridge_core.final_reader_executor import (
    PersistentActiveReaderExecutor,
)
from bridge_core.final_runtime_config import (
    load_final_runtime_configuration,
)
from bridge_core.final_runtime_orchestrator import (
    FinalRuntimeOrchestrator,
)
from bridge_core.final_worker_cycle import (
    FinalWorkerCycle,
)
from bridge_core.models import (
    ReaderDevice,
    RFIDSession,
)
from bridge_core.odoo_api_v1 import (
    OdooRFIDApiClient,
)


class Command(BaseCommand):
    help = (
        "Run the final Odoo-controlled RFID receiving worker. "
        "Fails closed unless physical-reader and Odoo contact are "
        "explicitly enabled and final bearer configuration is present."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one control cycle after startup verification.",
        )

        parser.add_argument(
            "--max-cycles",
            type=int,
            default=None,
            help="Run a finite number of control cycles.",
        )

    def handle(self, *args, **options):
        if options["once"] and options["max_cycles"] is not None:
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

        self._require_live_gates()

        configuration = (
            load_final_runtime_configuration(
                settings.CONFIGURATION
            )
        )

        try:
            device = ReaderDevice.objects.get(
                code=configuration.reader_code,
                enabled=True,
            )
        except ReaderDevice.DoesNotExist as exc:
            raise CommandError(
                "Configured final RFID reader does not exist or is disabled."
            ) from exc

        if (
            device.inventory_mode
            != ReaderDevice.InventoryMode.ACTIVE
        ):
            raise CommandError(
                "Final RFID reader must use active inventory mode."
            )

        api_client = OdooRFIDApiClient(
            base_url=configuration.odoo_base_url,
            bearer_token=configuration.bearer_token,
            gateway_code=configuration.gateway_code,
            timeout_seconds=(
                configuration.request_timeout_seconds
            ),
            verify_tls=configuration.verify_tls,
        )

        reader_executor = (
            PersistentActiveReaderExecutor(
                device=device,
            )
        )

        orchestrator = FinalRuntimeOrchestrator(
            api_client=api_client,
            reader_executor=reader_executor,
            reader_code=device.code,
        )

        worker = FinalWorkerCycle(
            orchestrator=orchestrator,
            reader_executor=reader_executor,
            device=device,
        )

        orchestrator.before_session_close = (
            worker.drain_pending_tags
        )

        orchestrator.before_success_ack = (
            worker.require_stop_delivery_complete
        )

        self.stdout.write(
            "RFID_FINAL_WORKER_STARTUP_STATE=offline"
        )

        # Always establish a physically safe idle state before deciding
        # whether a stale local session requires operator recovery.
        reader_executor.verify_idle()

        existing_active = RFIDSession.objects.filter(
            device=device,
            status=RFIDSession.Status.ACTIVE,
        ).count()

        if existing_active:
            raise CommandError(
                "Final RFID reader was forced to a verified idle state, "
                "but a stale active local session remains. "
                "Recovery is required before the worker may start."
            )

        orchestrator.mark_reader_verified_idle()

        self.stdout.write(
            "RFID_FINAL_READER_STATE=idle"
        )
        self.stdout.write(
            f"RFID_GATEWAY_CODE={configuration.gateway_code}"
        )
        self.stdout.write(
            f"RFID_READER_CODE={device.code}"
        )
        self.stdout.write(
            f"POLL_SECONDS={configuration.poll_seconds}"
        )

        cycle_number = 0

        try:
            while (
                max_cycles is None
                or cycle_number < max_cycles
            ):
                cycle_number += 1

                result = worker.run_once()

                self.stdout.write(
                    "CYCLE="
                    f"{cycle_number} "
                    "STATE="
                    f"{result.runtime_state} "
                    "COMMANDS="
                    f"{result.commands_processed} "
                    "FRAMES="
                    f"{result.tag_frames_received} "
                    "CREATED="
                    f"{result.tags_created}"
                )

                if (
                    max_cycles is not None
                    and cycle_number >= max_cycles
                ):
                    break

                time.sleep(
                    configuration.poll_seconds
                )

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "HOLD: final RFID worker stopped by operator"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: final RFID worker completed "
                f"{cycle_number} cycle(s)"
            )
        )

    @staticmethod
    def _require_live_gates():
        if not settings.ALLOW_PHYSICAL_READER_CONTACT:
            raise CommandError(
                "Final RFID worker requires "
                "ALLOW_PHYSICAL_READER_CONTACT=True."
            )

        if not settings.ALLOW_ODOO_CONTACT:
            raise CommandError(
                "Final RFID worker requires "
                "ALLOW_ODOO_CONTACT=True."
            )

        # The legacy worker remains intentionally fail-closed.
        if settings.SENDER_BACKEND != "disabled":
            raise CommandError(
                "Final RFID worker requires "
                "SENDER_BACKEND=disabled."
            )
