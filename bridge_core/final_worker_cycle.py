from dataclasses import dataclass

from bridge_core.final_delivery_barrier import (
    require_final_session_delivery_complete,
)
from bridge_core.final_runtime_state import RuntimeState
from bridge_core.final_tag_ingestion import (
    ingest_final_active_tag_frames,
)


class FinalWorkerCycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalWorkerCycleResult:
    heartbeat_sent: bool
    commands_processed: int
    tag_frames_received: int
    tags_created: int
    tags_duplicate: int
    tags_assigned: int
    runtime_state: str


class FinalWorkerCycle:
    """
    One deterministic iteration of the final RFID worker.

    The continuous management command will repeatedly call run_once().
    Network and reader activity are supplied by the already-tested
    orchestrator/executor objects.
    """

    def __init__(
        self,
        *,
        orchestrator,
        reader_executor,
        device,
        tag_ingestor=ingest_final_active_tag_frames,
        capture_service=None,
    ):
        self.orchestrator = orchestrator
        self.reader_executor = reader_executor
        self.device = device
        self.tag_ingestor = tag_ingestor
        self.capture_service = capture_service

    def start_capture(
        self,
        *,
        session_key,
        reader_code,
    ):
        if self.capture_service is None:
            return None

        return self.capture_service.start(
            session_key=session_key,
            reader_code=reader_code,
        )

    def stop_capture(
        self,
        *,
        session_key=None,
        reader_code=None,
    ):
        if (
            reader_code is not None
            and reader_code != self.device.code
        ):
            raise FinalWorkerCycleError(
                "Capture-stop reader identity mismatch."
            )

        if self.capture_service is None:
            return None

        active_capture_key = (
            self.capture_service.session_key
        )

        if (
            session_key
            and active_capture_key
            and active_capture_key != session_key
        ):
            raise FinalWorkerCycleError(
                "Capture-stop session identity mismatch."
            )

        self.capture_service.stop()
        return None

    def require_capture_healthy(self):
        if self.capture_service is None:
            return True

        return self.capture_service.require_healthy()

    def drain_pending_tags(
        self,
        *,
        session_key,
        reader_code,
    ):
        if reader_code != self.device.code:
            raise FinalWorkerCycleError(
                "Tag-drain reader identity mismatch."
            )

        frames = tuple(
            self.reader_executor.take_pending_tag_frames()
        )

        if not frames:
            return None

        return self.tag_ingestor(
            device=self.device,
            session_key=session_key,
            frames=frames,
        )

    def require_stop_delivery_complete(
        self,
        *,
        session_key,
        reader_code,
    ):
        if reader_code != self.device.code:
            raise FinalWorkerCycleError(
                "STOP delivery barrier reader identity mismatch."
            )

        return require_final_session_delivery_complete(
            session_key=session_key,
            reader_code=reader_code,
        )

    def _poll_active_tags(self):
        runtime = self.orchestrator.runtime

        if runtime.state != RuntimeState.READING:
            return None

        if not runtime.session_key:
            raise FinalWorkerCycleError(
                "Reading runtime has no session key."
            )

        frames = tuple(
            self.reader_executor.poll_tag_frames()
        )

        if not frames:
            return None

        return self.tag_ingestor(
            device=self.device,
            session_key=runtime.session_key,
            frames=frames,
        )

    def run_once(self):
        heartbeat_sent = False
        commands_processed = 0
        tag_frames_received = 0
        tags_created = 0
        tags_duplicate = 0
        tags_assigned = 0

        self.require_capture_healthy()

        self.orchestrator.heartbeat()
        heartbeat_sent = True

        # When the background capture service is configured, it is the
        # sole owner of active tag receiving while Odoo HTTP may block.
        # The synchronous polling path remains only for isolated/legacy
        # tests that do not supply a capture service.
        if self.capture_service is None:
            tag_result = self._poll_active_tags()

            if tag_result is not None:
                tag_frames_received += tag_result.frame_count
                tags_created += tag_result.created_count
                tags_duplicate += tag_result.duplicate_count
                tags_assigned += tag_result.assigned_count

        command_results = self.orchestrator.poll_commands()
        commands_processed = len(command_results)

        runtime = self.orchestrator.runtime

        if self.capture_service is None:
            # Legacy/test synchronous path: persist frames buffered during
            # the START response immediately.
            if (
                runtime.state == RuntimeState.READING
                and runtime.session_key
            ):
                pending = tuple(
                    self.reader_executor.take_pending_tag_frames()
                )

                if pending:
                    pending_result = self.tag_ingestor(
                        device=self.device,
                        session_key=runtime.session_key,
                        frames=pending,
                    )

                    tag_frames_received += pending_result.frame_count
                    tags_created += pending_result.created_count
                    tags_duplicate += pending_result.duplicate_count
                    tags_assigned += pending_result.assigned_count

        self.require_capture_healthy()

        return FinalWorkerCycleResult(
            heartbeat_sent=heartbeat_sent,
            commands_processed=commands_processed,
            tag_frames_received=tag_frames_received,
            tags_created=tags_created,
            tags_duplicate=tags_duplicate,
            tags_assigned=tags_assigned,
            runtime_state=self.orchestrator.runtime.state.value,
        )
