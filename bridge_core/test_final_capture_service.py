import time
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from bridge_core.final_capture_service import (
    FinalCaptureService,
    FinalCaptureServiceError,
)


class FakeReaderExecutor:
    def __init__(self):
        self.is_active = True
        self.active_session_key = "session-001"
        self.frames = []
        self.poll_count = 0
        self.error = None

    def poll_tag_frames(self):
        self.poll_count += 1

        if self.error is not None:
            raise self.error

        if not self.frames:
            return ()

        frames = tuple(self.frames)
        self.frames.clear()
        return frames


class FinalCaptureServiceTests(SimpleTestCase):
    def setUp(self):
        self.reader = FakeReaderExecutor()
        self.device = SimpleNamespace(
            code="receiving-door-01",
        )
        self.ingestor = Mock()

        self.service = FinalCaptureService(
            reader_executor=self.reader,
            device=self.device,
            tag_ingestor=self.ingestor,
            idle_sleep_seconds=0.001,
            join_timeout_seconds=1.0,
        )

    def tearDown(self):
        if self.service.is_running:
            self.service.stop()

    def wait_until(
        self,
        predicate,
        timeout=1.0,
    ):
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if predicate():
                return

            time.sleep(0.001)

        self.fail(
            "Timed out waiting for capture service condition."
        )

    def test_capture_persists_frames_without_control_thread_poll(self):
        self.reader.frames = [
            "tag-a",
            "tag-b",
        ]

        self.service.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.wait_until(
            lambda: self.ingestor.call_count == 1
        )

        self.ingestor.assert_called_once_with(
            device=self.device,
            session_key="session-001",
            frames=("tag-a", "tag-b"),
        )

        self.assertGreater(
            self.reader.poll_count,
            0,
        )

    def test_capture_continues_until_explicit_stop(self):
        self.service.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.wait_until(
            lambda: self.reader.poll_count > 2
        )

        self.assertTrue(
            self.service.is_running
        )

        self.service.stop()

        self.assertFalse(
            self.service.is_running
        )
        self.assertIsNone(
            self.service.session_key
        )

    def test_same_session_start_is_idempotent(self):
        self.service.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        first_thread = self.service._thread

        self.service.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertIs(
            self.service._thread,
            first_thread,
        )

    def test_different_session_is_rejected(self):
        self.service.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        with self.assertRaisesMessage(
            FinalCaptureServiceError,
            "different session",
        ):
            self.service.start(
                session_key="session-002",
                reader_code="receiving-door-01",
            )

    def test_inactive_reader_is_rejected(self):
        self.reader.is_active = False

        with self.assertRaisesMessage(
            FinalCaptureServiceError,
            "reader is inactive",
        ):
            self.service.start(
                session_key="session-001",
                reader_code="receiving-door-01",
            )

    def test_reader_session_identity_must_match(self):
        self.reader.active_session_key = (
            "different-session"
        )

        with self.assertRaisesMessage(
            FinalCaptureServiceError,
            "does not match active reader session",
        ):
            self.service.start(
                session_key="session-001",
                reader_code="receiving-door-01",
            )

    def test_reader_failure_is_retained_for_control_thread(self):
        self.reader.error = RuntimeError(
            "simulated reader failure"
        )

        self.service.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.wait_until(
            lambda: self.service.error is not None
        )

        with self.assertRaisesMessage(
            FinalCaptureServiceError,
            "simulated reader failure",
        ):
            self.service.require_healthy()

    def test_ingestion_failure_is_retained_for_control_thread(self):
        self.reader.frames = ["tag-a"]

        self.ingestor.side_effect = RuntimeError(
            "simulated persistence failure"
        )

        self.service.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.wait_until(
            lambda: self.service.error is not None
        )

        with self.assertRaisesMessage(
            FinalCaptureServiceError,
            "simulated persistence failure",
        ):
            self.service.require_healthy()

    def test_wrong_reader_identity_is_rejected(self):
        with self.assertRaisesMessage(
            FinalCaptureServiceError,
            "identity mismatch",
        ):
            self.service.start(
                session_key="session-001",
                reader_code="wrong-reader",
            )
