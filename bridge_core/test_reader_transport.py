from unittest.mock import Mock, call

from django.test import SimpleTestCase

from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    COMMAND_HANDSHAKE,
    COMMAND_INVENTORY_STATISTICS,
    build_frame,
)
from bridge_core.reader_transport import (
    RFIDTransportError,
    TCPReaderConnection,
    TCPReaderTransport,
    validate_connection,
)


class TCPReaderTransportTests(SimpleTestCase):
    @staticmethod
    def _connection(**overrides):
        values = {
            "host": "192.168.1.200",
            "port": 8090,
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 3,
        }
        values.update(overrides)
        return TCPReaderConnection(**values)

    def test_empty_host_is_rejected(self):
        with self.assertRaisesMessage(
            RFIDTransportError,
            "host cannot be empty",
        ):
            validate_connection(
                self._connection(host="   ")
            )

    def test_invalid_port_is_rejected(self):
        with self.assertRaisesMessage(
            RFIDTransportError,
            "between 1 and 65535",
        ):
            validate_connection(
                self._connection(port=65536)
            )

    def test_invalid_timeouts_are_rejected(self):
        with self.assertRaisesMessage(
            RFIDTransportError,
            "connect timeout",
        ):
            validate_connection(
                self._connection(
                    connect_timeout_seconds=0
                )
            )

        with self.assertRaisesMessage(
            RFIDTransportError,
            "read timeout",
        ):
            validate_connection(
                self._connection(
                    read_timeout_seconds=301
                )
            )

    def test_exchange_sends_frame_and_returns_matching_response(self):
        outbound = build_frame(
            address=1,
            sequence=1,
            command=COMMAND_HANDSHAKE,
        )
        response = build_frame(
            address=1,
            sequence=1,
            command=COMMAND_HANDSHAKE,
        )

        fake_socket = Mock()
        fake_socket.recv.side_effect = (
            response[:4],
            response[4:],
        )
        socket_factory = Mock(
            return_value=fake_socket
        )

        transport = TCPReaderTransport(
            connection=self._connection(),
            socket_factory=socket_factory,
        )

        frames = transport.exchange(
            outbound_frame=outbound,
            expected_commands=(COMMAND_HANDSHAKE,),
        )

        socket_factory.assert_called_once_with(
            ("192.168.1.200", 8090),
            5,
        )
        fake_socket.settimeout.assert_called_once_with(3)
        fake_socket.sendall.assert_called_once_with(outbound)
        fake_socket.close.assert_called_once_with()

        self.assertEqual(len(frames), 1)
        self.assertEqual(
            frames[0].command,
            COMMAND_HANDSHAKE,
        )

    def test_unexpected_frame_is_ignored_until_expected_frame(self):
        outbound = build_frame(
            address=1,
            sequence=2,
            command=COMMAND_HANDSHAKE,
        )
        unexpected = build_frame(
            address=1,
            sequence=2,
            command=COMMAND_INVENTORY_STATISTICS,
        )
        expected = build_frame(
            address=1,
            sequence=2,
            command=COMMAND_HANDSHAKE,
        )

        fake_socket = Mock()
        fake_socket.recv.side_effect = (
            unexpected,
            expected,
        )

        transport = TCPReaderTransport(
            connection=self._connection(),
            socket_factory=Mock(
                return_value=fake_socket
            ),
        )

        frames = transport.exchange(
            outbound_frame=outbound,
            expected_commands=(COMMAND_HANDSHAKE,),
        )

        self.assertEqual(len(frames), 1)
        self.assertEqual(
            frames[0].command,
            COMMAND_HANDSHAKE,
        )
        fake_socket.close.assert_called_once_with()

    def test_incomplete_response_fails_closed(self):
        outbound = build_frame(
            address=1,
            sequence=3,
            command=COMMAND_HANDSHAKE,
        )
        response = build_frame(
            address=1,
            sequence=3,
            command=COMMAND_HANDSHAKE,
        )

        fake_socket = Mock()
        fake_socket.recv.side_effect = (
            response[:5],
            b"",
        )

        transport = TCPReaderTransport(
            connection=self._connection(),
            socket_factory=Mock(
                return_value=fake_socket
            ),
        )

        with self.assertRaisesMessage(
            RFIDTransportError,
            "incomplete RFID frame",
        ):
            transport.exchange(
                outbound_frame=outbound,
                expected_commands=(
                    COMMAND_HANDSHAKE,
                ),
            )

        fake_socket.close.assert_called_once_with()

    def test_connection_error_is_wrapped_and_socket_not_leaked(self):
        socket_factory = Mock(
            side_effect=OSError("network unreachable")
        )

        transport = TCPReaderTransport(
            connection=self._connection(),
            socket_factory=socket_factory,
        )

        with self.assertRaisesMessage(
            RFIDTransportError,
            "network unreachable",
        ):
            transport.exchange(
                outbound_frame=b"frame",
                expected_commands=(
                    COMMAND_HANDSHAKE,
                ),
            )

    def test_empty_outbound_frame_is_rejected_before_connection(self):
        socket_factory = Mock()

        transport = TCPReaderTransport(
            connection=self._connection(),
            socket_factory=socket_factory,
        )

        with self.assertRaisesMessage(
            RFIDTransportError,
            "cannot be empty",
        ):
            transport.exchange(
                outbound_frame=b"",
                expected_commands=(
                    COMMAND_HANDSHAKE,
                ),
            )

        socket_factory.assert_not_called()

    def test_persistent_session_reuses_one_socket_for_multiple_commands(self):
        first_outbound = build_frame(
            address=1,
            sequence=1,
            command=COMMAND_HANDSHAKE,
        )
        second_outbound = build_frame(
            address=1,
            sequence=2,
            command=COMMAND_INVENTORY_STATISTICS,
        )
        first_response = build_frame(
            address=1,
            sequence=1,
            command=COMMAND_HANDSHAKE,
        )
        second_response = build_frame(
            address=1,
            sequence=2,
            command=COMMAND_INVENTORY_STATISTICS,
        )

        fake_socket = Mock()
        fake_socket.recv.side_effect = (
            first_response,
            second_response,
        )
        socket_factory = Mock(
            return_value=fake_socket
        )

        transport = TCPReaderTransport(
            connection=self._connection(),
            socket_factory=socket_factory,
        )

        with transport.open_session() as session:
            first_frames = session.exchange(
                outbound_frame=first_outbound,
                expected_commands=(
                    COMMAND_HANDSHAKE,
                ),
            )
            second_frames = session.exchange(
                outbound_frame=second_outbound,
                expected_commands=(
                    COMMAND_INVENTORY_STATISTICS,
                ),
            )

            self.assertTrue(session.is_open)

        socket_factory.assert_called_once_with(
            ("192.168.1.200", 8090),
            5,
        )
        self.assertEqual(
            fake_socket.sendall.call_args_list,
            [
                call(first_outbound),
                call(second_outbound),
            ],
        )
        fake_socket.close.assert_called_once_with()

        self.assertEqual(
            first_frames[0].command,
            COMMAND_HANDSHAKE,
        )
        self.assertEqual(
            second_frames[0].command,
            COMMAND_INVENTORY_STATISTICS,
        )
        self.assertFalse(session.is_open)

    def test_session_operations_fail_when_not_open(self):
        transport = TCPReaderTransport(
            connection=self._connection(),
            socket_factory=Mock(),
        )
        session = transport.open_session()

        with self.assertRaisesMessage(
            RFIDTransportError,
            "session is not open",
        ):
            session.send(b"frame")

        with self.assertRaisesMessage(
            RFIDTransportError,
            "session is not open",
        ):
            session.receive(
                expected_commands=(
                    COMMAND_HANDSHAKE,
                ),
            )


class TCPReaderSessionTimeoutBehaviourTests(SimpleTestCase):
    def make_open_session(self, recv_side_effect):
        fake_socket = Mock()
        fake_socket.recv.side_effect = recv_side_effect

        socket_factory = Mock(return_value=fake_socket)

        transport = TCPReaderTransport(
            connection=TCPReaderConnection(
                host="192.168.1.201",
                port=8090,
                connect_timeout_seconds=5,
                read_timeout_seconds=5,
            ),
            socket_factory=socket_factory,
        )

        session = transport.open_session()
        session.__enter__()

        self.addCleanup(session.close)

        return session

    def test_timeout_can_return_empty_for_active_polling(self):
        import socket

        session = self.make_open_session(
            socket.timeout("quiet reader period")
        )

        frames = session.receive(
            expected_commands=(COMMAND_ACTIVE_TAG,),
            timeout_returns_empty=True,
        )

        self.assertEqual(frames, ())

    def test_timeout_remains_failure_by_default(self):
        import socket

        session = self.make_open_session(
            socket.timeout("reader timed out")
        )

        with self.assertRaisesMessage(
            RFIDTransportError,
            "RFID TCP receive failed",
        ):
            session.receive(
                expected_commands=(COMMAND_ACTIVE_TAG,),
            )

    def test_os_error_remains_failure_when_timeout_mode_enabled(self):
        session = self.make_open_session(
            OSError("connection reset")
        )

        with self.assertRaisesMessage(
            RFIDTransportError,
            "connection reset",
        ):
            session.receive(
                expected_commands=(COMMAND_ACTIVE_TAG,),
                timeout_returns_empty=True,
            )
