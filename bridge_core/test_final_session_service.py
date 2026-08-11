from django.test import TestCase

from bridge_core.final_runtime_state import (
    OdooRFIDCommand,
)
from bridge_core.final_session_service import (
    FinalSessionError,
    cancel_local_session,
    close_local_session,
    synchronize_start_command,
)
from bridge_core.models import ReaderDevice, RFIDSession


class FinalSessionServiceTests(TestCase):
    def setUp(self):
        self.reader = ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            enabled=True,
        )

    def command(
        self,
        *,
        session_key="odoo-session-001",
        reader_code="receiving-door-01",
        picking="EXWS1/IN/02227",
    ):
        return OdooRFIDCommand.from_payload(
            {
                "session_key": session_key,
                "reader_code": reader_code,
                "command": "start",
                "revision": 1,
                "picking": picking,
            }
        )

    def test_start_creates_active_local_session(self):
        result = synchronize_start_command(
            command=self.command(),
        )

        self.assertTrue(result.created)
        self.assertFalse(result.reused)
        self.assertEqual(
            result.session.status,
            RFIDSession.Status.ACTIVE,
        )
        self.assertEqual(
            result.session.external_session_key,
            "odoo-session-001",
        )
        self.assertEqual(
            result.session.odoo_reference,
            "EXWS1/IN/02227",
        )
        self.assertEqual(
            result.session.odoo_record_id,
            0,
        )

    def test_same_start_is_idempotently_reused(self):
        first = synchronize_start_command(
            command=self.command(),
        )
        second = synchronize_start_command(
            command=self.command(),
        )

        self.assertTrue(first.created)
        self.assertTrue(second.reused)
        self.assertEqual(
            first.session.id,
            second.session.id,
        )
        self.assertEqual(
            RFIDSession.objects.count(),
            1,
        )

    def test_different_active_session_blocks_start(self):
        RFIDSession.objects.create(
            external_session_key="existing-session",
            device=self.reader,
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_record_id=0,
            odoo_reference="OLD/IN/0001",
            status=RFIDSession.Status.ACTIVE,
        )

        with self.assertRaises(FinalSessionError):
            synchronize_start_command(
                command=self.command(
                    session_key="new-session"
                )
            )

    def test_wrong_reader_is_rejected(self):
        with self.assertRaises(FinalSessionError):
            synchronize_start_command(
                command=self.command(
                    reader_code="missing-reader"
                )
            )

    def test_closed_session_cannot_be_restarted(self):
        RFIDSession.objects.create(
            external_session_key="odoo-session-001",
            device=self.reader,
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_record_id=0,
            status=RFIDSession.Status.CLOSED,
        )

        with self.assertRaises(FinalSessionError):
            synchronize_start_command(
                command=self.command()
            )

    def test_close_session(self):
        session = synchronize_start_command(
            command=self.command(),
        ).session

        closed = close_local_session(
            session_key=session.external_session_key,
            reader_code=self.reader.code,
        )

        self.assertEqual(
            closed.status,
            RFIDSession.Status.CLOSED,
        )
        self.assertIsNotNone(closed.closed_at)

    def test_close_is_idempotent(self):
        session = synchronize_start_command(
            command=self.command(),
        ).session

        close_local_session(
            session_key=session.external_session_key,
            reader_code=self.reader.code,
        )

        again = close_local_session(
            session_key=session.external_session_key,
            reader_code=self.reader.code,
        )

        self.assertEqual(
            again.status,
            RFIDSession.Status.CLOSED,
        )

    def test_cancel_session(self):
        session = synchronize_start_command(
            command=self.command(),
        ).session

        cancelled = cancel_local_session(
            session_key=session.external_session_key,
            reader_code=self.reader.code,
        )

        self.assertEqual(
            cancelled.status,
            RFIDSession.Status.CANCELLED,
        )

    def test_reader_role_controls_operation(self):
        result = synchronize_start_command(
            command=self.command(),
        )

        self.assertEqual(
            result.session.operation_type,
            RFIDSession.OperationType.RECEIPT,
        )
