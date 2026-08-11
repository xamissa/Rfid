from types import SimpleNamespace

from django.test import SimpleTestCase

from bridge_core.final_reader_executor import (
    FinalReaderExecutorError,
    PersistentActiveReaderExecutor,
)
from bridge_core.models import ReaderDevice
from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    COMMAND_START_INVENTORY,
    COMMAND_STOP,
    RFIDFrame,
    parse_frame,
)


def frame(
    *,
    command,
    status=0,
    address=2,
):
    return RFIDFrame(
        address=address,
        sequence=1,
        command=command,
        status=status,
        payload=b"",
        raw_frame=b"",
    )


class FakeSession:
    def __init__(self, batches):
        self.batches = list(batches)
        self.sent = []
        self.opened = False
        self.closed = False

    def __enter__(self):
        self.opened = True
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.close()

    def send(self, outbound_frame):
        self.sent.append(outbound_frame)

    def receive(
        self,
        *,
        expected_commands,
        maximum_reads=32,
        timeout_returns_empty=False,
    ):
        del expected_commands
        del maximum_reads
        del timeout_returns_empty

        if not self.batches:
            return ()

        return tuple(self.batches.pop(0))

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(
        self,
        *,
        connection,
        session,
    ):
        self.connection = connection
        self.session = session

    def open_session(self):
        return self.session


class RecordingTransportFactory:
    def __init__(self, session):
        self.session = session
        self.connections = []

    def __call__(self, *, connection):
        self.connections.append(connection)

        return FakeTransport(
            connection=connection,
            session=self.session,
        )


class PersistentActiveReaderExecutorTests(
    SimpleTestCase
):
    def device(self, **overrides):
        values = {
            "code": "receiving-door-01",
            "enabled": True,
            "inventory_mode": (
                ReaderDevice.InventoryMode.ACTIVE
            ),
            "host": "192.168.1.201",
            "port": 8090,
            "device_address": 2,
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 5,
        }

        values.update(overrides)

        return SimpleNamespace(**values)

    def executor(self, batches):
        session = FakeSession(batches)
        factory = RecordingTransportFactory(
            session
        )

        executor = PersistentActiveReaderExecutor(
            device=self.device(),
            transport_factory=factory,
        )

        return executor, session, factory

    def test_start_opens_persistent_session(self):
        executor, session, factory = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                        status=0,
                    )
                ]
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertTrue(executor.is_active)
        self.assertEqual(
            executor.active_session_key,
            "session-001",
        )
        self.assertTrue(session.opened)
        self.assertFalse(session.closed)
        self.assertEqual(
            len(factory.connections),
            1,
        )

        outbound = parse_frame(session.sent[0])

        self.assertEqual(
            outbound.command,
            COMMAND_START_INVENTORY,
        )
        self.assertEqual(
            outbound.address,
            2,
        )

    def test_start_status_0x99_is_accepted(self):
        executor, _, _ = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                        status=0x99,
                    )
                ]
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertTrue(executor.is_active)

    def test_start_rejection_fails_closed(self):
        executor, session, _ = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                        status=5,
                    )
                ]
            ]
        )

        with self.assertRaises(
            FinalReaderExecutorError
        ):
            executor.start(
                session_key="session-001",
                reader_code="receiving-door-01",
            )

        self.assertFalse(executor.is_active)
        self.assertTrue(session.closed)

    def test_start_buffers_early_tag_frame(self):
        executor, _, _ = self.executor(
            [
                [
                    frame(
                        command=COMMAND_ACTIVE_TAG,
                    ),
                ],
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                    ),
                ],
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        buffered = (
            executor.take_pending_tag_frames()
        )

        self.assertEqual(len(buffered), 1)
        self.assertEqual(
            buffered[0].command,
            COMMAND_ACTIVE_TAG,
        )

    def test_stop_uses_same_session_and_closes(self):
        executor, session, factory = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                    )
                ],
                [
                    frame(
                        command=COMMAND_STOP,
                    )
                ],
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        executor.stop(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertFalse(executor.is_active)
        self.assertTrue(session.closed)
        self.assertEqual(
            len(factory.connections),
            1,
        )

        commands = [
            parse_frame(raw).command
            for raw in session.sent
        ]

        self.assertEqual(
            commands,
            [
                COMMAND_START_INVENTORY,
                COMMAND_STOP,
            ],
        )

    def test_tag_before_stop_is_preserved(self):
        executor, _, _ = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                    )
                ],
                [
                    frame(
                        command=COMMAND_ACTIVE_TAG,
                    )
                ],
                [
                    frame(
                        command=COMMAND_STOP,
                    )
                ],
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        executor.stop(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        buffered = (
            executor.take_pending_tag_frames()
        )

        self.assertEqual(len(buffered), 1)

    def test_poll_returns_active_tag_frames(self):
        executor, _, _ = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                    )
                ],
                [
                    frame(
                        command=COMMAND_ACTIVE_TAG,
                    ),
                    frame(
                        command=COMMAND_ACTIVE_TAG,
                    ),
                ],
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        frames = executor.poll_tag_frames()

        self.assertEqual(len(frames), 2)

    def test_wrong_reader_is_rejected_without_connection(self):
        executor, _, factory = self.executor([])

        with self.assertRaises(
            FinalReaderExecutorError
        ):
            executor.start(
                session_key="session-001",
                reader_code="wrong-reader",
            )

        self.assertEqual(
            factory.connections,
            [],
        )

    def test_different_second_start_is_blocked(self):
        executor, _, _ = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                    )
                ]
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        with self.assertRaises(
            FinalReaderExecutorError
        ):
            executor.start(
                session_key="session-002",
                reader_code="receiving-door-01",
            )

    def test_same_start_is_idempotent(self):
        executor, session, factory = self.executor(
            [
                [
                    frame(
                        command=COMMAND_START_INVENTORY,
                    )
                ]
            ]
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        executor.start(
            session_key="session-001",
            reader_code="receiving-door-01",
        )

        self.assertEqual(
            len(factory.connections),
            1,
        )
        self.assertEqual(
            len(session.sent),
            1,
        )

    def test_active_mode_is_required(self):
        with self.assertRaises(
            FinalReaderExecutorError
        ):
            PersistentActiveReaderExecutor(
                device=self.device(
                    inventory_mode=(
                        ReaderDevice.InventoryMode.CACHED
                    )
                ),
            )

    def test_disabled_reader_is_rejected(self):
        with self.assertRaises(
            FinalReaderExecutorError
        ):
            PersistentActiveReaderExecutor(
                device=self.device(
                    enabled=False
                ),
            )
