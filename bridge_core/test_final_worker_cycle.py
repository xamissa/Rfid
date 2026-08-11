from dataclasses import dataclass
from types import SimpleNamespace

from django.test import SimpleTestCase

from bridge_core.final_runtime_state import (
    LocalReaderRuntime,
    RuntimeState,
)
from bridge_core.final_worker_cycle import (
    FinalWorkerCycle,
    FinalWorkerCycleError,
)


@dataclass(frozen=True)
class FakeIngestionResult:
    frame_count: int
    created_count: int
    duplicate_count: int
    assigned_count: int


class FakeReaderExecutor:
    def __init__(self):
        self.polled = []
        self.pending = []

    def poll_tag_frames(self):
        frames = tuple(self.polled)
        self.polled.clear()
        return frames

    def take_pending_tag_frames(self):
        frames = tuple(self.pending)
        self.pending.clear()
        return frames


class FakeOrchestrator:
    def __init__(self):
        self.runtime = LocalReaderRuntime(
            reader_code="receiving-door-01",
            state=RuntimeState.IDLE,
            session_key=None,
            last_command_revision=0,
            error=None,
        )
        self.heartbeat_count = 0
        self.command_results = ()

    def heartbeat(self):
        self.heartbeat_count += 1
        return {"ok": True}

    def poll_commands(self):
        return tuple(self.command_results)


class RecordingTagIngestor:
    def __init__(self):
        self.calls = []

    def __call__(
        self,
        *,
        device,
        session_key,
        frames,
    ):
        frames = tuple(frames)

        self.calls.append(
            {
                "device": device,
                "session_key": session_key,
                "frames": frames,
            }
        )

        return FakeIngestionResult(
            frame_count=len(frames),
            created_count=len(frames),
            duplicate_count=0,
            assigned_count=len(frames),
        )


class FinalWorkerCycleTests(SimpleTestCase):
    def setUp(self):
        self.device = SimpleNamespace(
            code="receiving-door-01"
        )
        self.orchestrator = FakeOrchestrator()
        self.reader = FakeReaderExecutor()
        self.ingestor = RecordingTagIngestor()

        self.worker = FinalWorkerCycle(
            orchestrator=self.orchestrator,
            reader_executor=self.reader,
            device=self.device,
            tag_ingestor=self.ingestor,
        )

    def test_idle_cycle_sends_heartbeat_and_polls_commands(self):
        self.orchestrator.command_results = (
            SimpleNamespace(success=True),
        )

        result = self.worker.run_once()

        self.assertTrue(result.heartbeat_sent)
        self.assertEqual(
            self.orchestrator.heartbeat_count,
            1,
        )
        self.assertEqual(
            result.commands_processed,
            1,
        )
        self.assertEqual(
            result.tag_frames_received,
            0,
        )

    def test_reading_cycle_persists_polled_frames(self):
        self.orchestrator.runtime = LocalReaderRuntime(
            reader_code="receiving-door-01",
            state=RuntimeState.READING,
            session_key="session-001",
            last_command_revision=1,
            error=None,
        )

        self.reader.polled = ["tag-a", "tag-b"]

        result = self.worker.run_once()

        self.assertEqual(
            result.tag_frames_received,
            2,
        )
        self.assertEqual(
            result.tags_created,
            2,
        )
        self.assertEqual(
            self.ingestor.calls[0]["session_key"],
            "session-001",
        )

    def test_start_buffered_frames_are_persisted_same_cycle(self):
        def poll_commands():
            self.orchestrator.runtime = LocalReaderRuntime(
                reader_code="receiving-door-01",
                state=RuntimeState.READING,
                session_key="session-001",
                last_command_revision=1,
                error=None,
            )
            self.reader.pending = ["early-tag"]
            return (SimpleNamespace(success=True),)

        self.orchestrator.poll_commands = poll_commands

        result = self.worker.run_once()

        self.assertEqual(
            result.tag_frames_received,
            1,
        )
        self.assertEqual(
            self.ingestor.calls[-1]["frames"],
            ("early-tag",),
        )

    def test_preclose_drain_persists_stop_buffered_frames(self):
        self.reader.pending = ["late-tag"]

        result = self.worker.drain_pending_tags(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertEqual(
            result.created_count,
            1,
        )
        self.assertEqual(
            self.ingestor.calls[-1]["session_key"],
            "session-001",
        )

    def test_preclose_drain_empty_is_safe(self):
        result = self.worker.drain_pending_tags(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertIsNone(result)

    def test_preclose_wrong_reader_is_rejected(self):
        with self.assertRaises(
            FinalWorkerCycleError
        ):
            self.worker.drain_pending_tags(
                session_key="session-001",
                reader_code="wrong-reader",
            )

    def test_nonreading_cycle_does_not_poll_reader_tags(self):
        self.orchestrator.runtime = LocalReaderRuntime(
            reader_code="receiving-door-01",
            state=RuntimeState.OFFLINE,
            session_key=None,
            last_command_revision=0,
            error="unverified",
        )
        self.reader.polled = ["must-not-be-read"]

        result = self.worker.run_once()

        self.assertEqual(
            result.tag_frames_received,
            0,
        )
        self.assertEqual(
            self.reader.polled,
            ["must-not-be-read"],
        )


class FakeCaptureService:
    def __init__(self):
        self.start_calls = []
        self.stop_calls = 0
        self.health_checks = 0
        self.session_key = None

    def start(
        self,
        *,
        session_key,
        reader_code,
    ):
        self.session_key = session_key
        self.start_calls.append(
            (session_key, reader_code)
        )

    def stop(self):
        self.stop_calls += 1
        self.session_key = None

    def require_healthy(self):
        self.health_checks += 1
        return True


class FinalWorkerBackgroundCaptureCycleTests(
    SimpleTestCase
):
    def setUp(self):
        self.device = SimpleNamespace(
            code="receiving-door-01"
        )
        self.orchestrator = FakeOrchestrator()
        self.reader = FakeReaderExecutor()
        self.ingestor = RecordingTagIngestor()
        self.capture = FakeCaptureService()

        self.worker = FinalWorkerCycle(
            orchestrator=self.orchestrator,
            reader_executor=self.reader,
            device=self.device,
            tag_ingestor=self.ingestor,
            capture_service=self.capture,
        )

    def test_background_capture_owns_reader_polling(self):
        self.orchestrator.runtime = LocalReaderRuntime(
            reader_code="receiving-door-01",
            state=RuntimeState.READING,
            session_key="session-001",
            last_command_revision=1,
            error=None,
        )

        self.reader.polled = [
            "must-not-be-polled-by-control-thread"
        ]

        result = self.worker.run_once()

        self.assertEqual(
            result.tag_frames_received,
            0,
        )

        self.assertEqual(
            self.reader.polled,
            ["must-not-be-polled-by-control-thread"],
        )

        self.assertGreaterEqual(
            self.capture.health_checks,
            2,
        )

    def test_capture_start_and_stop_delegate_session_identity(self):
        self.worker.start_capture(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertEqual(
            self.capture.start_calls,
            [
                (
                    "session-001",
                    "receiving-door-01",
                )
            ],
        )

        self.worker.stop_capture(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertEqual(
            self.capture.stop_calls,
            1,
        )
