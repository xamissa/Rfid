"""Public stream state and receive deadlines, using only an in-memory socket."""
import socket
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from bridge_core.reader_protocol import COMMAND_ACTIVE_TAG, build_frame
from bridge_core.reader_transport import TCPReaderConnection, TCPReaderTransport
from bridge_core.final_reader_executor import PersistentActiveReaderExecutor
from bridge_core.models import ReaderDevice


class ReceiveBudgetTests(SimpleTestCase):
    def setUp(self):
        self.socket = Mock()
        self.session = TCPReaderTransport(
            connection=TCPReaderConnection('offline.invalid', 8090, 5, 300),
            socket_factory=Mock(return_value=self.socket)).open_session()
        self.session.__enter__()
        self.addCleanup(self.session.close)

    def test_public_partial_state_tracks_fragment_and_completion(self):
        frame = build_frame(address=2, sequence=1, command=COMMAND_ACTIVE_TAG)
        self.socket.recv.side_effect = [frame[:5], socket.timeout(), frame[5:]]
        self.assertFalse(self.session.has_partial_frame)
        self.assertEqual(self.session.receive(expected_commands=(COMMAND_ACTIVE_TAG,),
                                             timeout_returns_empty=True), ())
        self.assertTrue(self.session.has_partial_frame)
        self.assertEqual(len(self.session.receive(expected_commands=(COMMAND_ACTIVE_TAG,))), 1)
        self.assertFalse(self.session.has_partial_frame)
        with self.assertRaises(AttributeError):
            self.session.has_partial_frame = True

    @patch('bridge_core.reader_transport.time.monotonic')
    def test_total_budget_caps_multiple_blocking_reads_and_restores_timeout(self, clock):
        clock.side_effect = [10.0, 10.0, 10.03, 10.06]
        frame = build_frame(address=2, sequence=1, command=COMMAND_ACTIVE_TAG)
        self.socket.recv.side_effect = [frame[:2], frame[2:4]]
        self.assertEqual(self.session.receive(
            expected_commands=(COMMAND_ACTIVE_TAG,), timeout_returns_empty=True,
            timeout_seconds=0.05), ())
        self.assertEqual(self.socket.recv.call_count, 2)
        values = [call.args[0] for call in self.socket.settimeout.call_args_list]
        self.assertAlmostEqual(values[1], 0.05)
        self.assertAlmostEqual(values[2], 0.02)
        self.assertEqual(values[-1], 300)
        self.assertTrue(self.session.has_partial_frame)

    def test_timeout_budget_restored_on_socket_failure(self):
        self.socket.recv.side_effect = OSError('broken')
        with self.assertRaisesRegex(RuntimeError, 'broken'):
            self.session.receive(expected_commands=(COMMAND_ACTIVE_TAG,),
                                 timeout_seconds=0.05)
        self.socket.settimeout.assert_called_with(300)

    def test_executor_uses_only_public_session_state(self):
        executor = PersistentActiveReaderExecutor(device=SimpleNamespace(
            enabled=True, inventory_mode=ReaderDevice.InventoryMode.ACTIVE,
            host='offline.invalid'))
        self.assertFalse(executor.has_partial_frame)
        executor._session = SimpleNamespace(has_partial_frame=True)
        self.assertTrue(executor.has_partial_frame)
        executor._session.has_partial_frame = False
        self.assertFalse(executor.has_partial_frame)

    def test_invalid_receive_budgets_are_rejected_before_reading(self):
        for budget in (0, -1, float('inf'), float('nan')):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(RuntimeError, 'finite and positive'):
                    self.session.receive(expected_commands=(COMMAND_ACTIVE_TAG,),
                                         timeout_seconds=budget)
        self.socket.recv.assert_not_called()
