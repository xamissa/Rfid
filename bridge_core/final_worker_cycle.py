from dataclasses import dataclass

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
    ):
        self.orchestrator = orchestrator
        self.reader_executor = reader_executor
        self.device = device
        self.tag_ingestor = tag_ingestor

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

        self.orchestrator.heartbeat()
        heartbeat_sent = True

        # Persist tag observations already waiting on an active connection
        # before handling a possible STOP command.
        tag_result = self._poll_active_tags()

        if tag_result is not None:
            tag_frames_received += tag_result.frame_count
            tags_created += tag_result.created_count
            tags_duplicate += tag_result.duplicate_count
            tags_assigned += tag_result.assigned_count

        command_results = self.orchestrator.poll_commands()
        commands_processed = len(command_results)

        # A START command may have buffered tag frames while the reader's
        # START response was being received. Persist those immediately.
        runtime = self.orchestrator.runtime

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

        return FinalWorkerCycleResult(
            heartbeat_sent=heartbeat_sent,
            commands_processed=commands_processed,
            tag_frames_received=tag_frames_received,
            tags_created=tags_created,
            tags_duplicate=tags_duplicate,
            tags_assigned=tags_assigned,
            runtime_state=self.orchestrator.runtime.state.value,
        )
