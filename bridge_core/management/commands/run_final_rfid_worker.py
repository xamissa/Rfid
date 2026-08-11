import signal
import time

from django.conf import settings
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from bridge_core.final_capture_service import (
    FinalCaptureService,
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
from bridge_core.final_tag_ingestion import (
    ingest_final_active_tag_frames,
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

        capture_service = FinalCaptureService(
            reader_executor=reader_executor,
            device=device,
            tag_ingestor=ingest_final_active_tag_frames,
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
            capture_service=capture_service,
        )

        orchestrator.after_reader_start = (
            worker.start_capture
        )

        orchestrator.before_reader_stop = (
            worker.stop_capture
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
        interrupted = False

        previous_sigterm_handler = signal.signal(
            signal.SIGTERM,
            self._handle_termination_signal,
        )

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
            interrupted = True

            self.stdout.write(
                self.style.WARNING(
                    "HOLD: final RFID worker shutdown requested"
                )
            )

        finally:
            signal.signal(
                signal.SIGTERM,
                previous_sigterm_handler,
            )

            stopped_active_session = (
                self._safe_shutdown_reader(
                    reader_executor=reader_executor,
                    worker=worker,
                    reader_code=device.code,
                )
            )

            if stopped_active_session:
                interrupted = True

                self.stdout.write(
                    self.style.WARNING(
                        "HOLD: active RFID reader was physically "
                        "stopped during worker shutdown; local "
                        "session remains active for controlled recovery"
                    )
                )

        if interrupted:
            return

        self.stdout.write(
            self.style.SUCCESS(
                "PASS: final RFID worker completed "
                f"{cycle_number} cycle(s)"
            )
        )

    @staticmethod
    def _handle_termination_signal(signum, frame):
        del signum, frame
        raise KeyboardInterrupt

    @staticmethod
    def _safe_shutdown_reader(
        *,
        reader_executor,
        worker,
        reader_code,
    ):
        if not reader_executor.is_active:
            worker.stop_capture()
            reader_executor.close()
            return False

        session_key = str(
            reader_executor.active_session_key or ""
        ).strip()

        if not session_key:
            reader_executor.close()
            raise CommandError(
                "RFID reader shutdown found an active connection "
                "without a session key."
            )

        try:
            worker.stop_capture(
                session_key=session_key,
                reader_code=reader_code,
            )

            reader_executor.stop(
                session_key=session_key,
                reader_code=reader_code,
            )
        except Exception as exc:
            raise CommandError(
                "Failed to verify physical RFID STOP during "
                f"worker shutdown: {exc}"
            ) from exc

        try:
            worker.drain_pending_tags(
                session_key=session_key,
                reader_code=reader_code,
            )
        except Exception as exc:
            raise CommandError(
                "RFID reader stopped, but final buffered tag "
                f"persistence failed during shutdown: {exc}"
            ) from exc

        return True

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
