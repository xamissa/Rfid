from django.test import TestCase

from bridge_core.final_runtime_state import (
    OdooRFIDCommand,
    RuntimeStateError,
)
from bridge_core.final_session_service import (
    FinalSessionError,
    synchronize_start_command,
)
from bridge_core.models import (
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)
from bridge_core.session_assignment import (
    assign_event_to_active_session,
)


class SharedReaderCommandContractTests(TestCase):
    def payload(self, **updates):
        payload = {
            "session_key": "shared-session-001",
            "reader_code": "receiving-door-01",
            "command": "start",
            "revision": 1,
            "picking": "EXWS1/IN/00001",
        }
        payload.update(updates)
        return payload

    def test_missing_operation_remains_backward_compatible(self):
        command = OdooRFIDCommand.from_payload(self.payload())
        self.assertIsNone(command.operation)

    def test_receipt_operation_is_parsed(self):
        command = OdooRFIDCommand.from_payload(
            self.payload(operation="receipt")
        )
        self.assertEqual(command.operation, "receipt")

    def test_dispatch_operation_is_parsed(self):
        command = OdooRFIDCommand.from_payload(
            self.payload(
                operation="dispatch",
                picking="EXWS1/OUT/00001",
            )
        )
        self.assertEqual(command.operation, "dispatch")

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(RuntimeStateError):
            OdooRFIDCommand.from_payload(
                self.payload(operation="something-else")
            )


class SharedReaderLifecycleContractTests(TestCase):
    def payload(self, *, command, operation):
        return {
            "session_key": "shared-lifecycle-session",
            "reader_code": "receiving-door-01",
            "command": command,
            "revision": 2,
            "picking": "EXWS1/OUT/00001",
            "operation": operation,
        }

    def test_stop_payload_accepts_dispatch_operation(self):
        command = OdooRFIDCommand.from_payload(
            self.payload(
                command="stop",
                operation="dispatch",
            )
        )

        self.assertEqual(command.command.value, "stop")
        self.assertEqual(command.operation, "dispatch")

    def test_abort_payload_accepts_dispatch_operation(self):
        command = OdooRFIDCommand.from_payload(
            self.payload(
                command="abort",
                operation="dispatch",
            )
        )

        self.assertEqual(command.command.value, "abort")
        self.assertEqual(command.operation, "dispatch")




class SharedReaderSessionTests(TestCase):
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
        session_key="shared-session-001",
        operation=None,
        picking="EXWS1/IN/00001",
    ):
        payload = {
            "session_key": session_key,
            "reader_code": self.reader.code,
            "command": "start",
            "revision": 1,
            "picking": picking,
        }

        if operation is not None:
            payload["operation"] = operation

        return OdooRFIDCommand.from_payload(payload)

    def test_existing_receiving_behaviour_is_unchanged(self):
        result = synchronize_start_command(
            command=self.command()
        )

        self.assertEqual(
            result.session.operation_type,
            RFIDSession.OperationType.RECEIPT,
        )

    def test_dedicated_receiving_reader_accepts_explicit_receipt(self):
        result = synchronize_start_command(
            command=self.command(operation="receipt")
        )

        self.assertEqual(
            result.session.operation_type,
            RFIDSession.OperationType.RECEIPT,
        )

    def test_dedicated_receiving_reader_rejects_dispatch(self):
        with self.assertRaises(FinalSessionError):
            synchronize_start_command(
                command=self.command(
                    operation="dispatch",
                    picking="EXWS1/OUT/00001",
                )
            )

    def test_shared_receiving_reader_accepts_dispatch(self):
        self.reader.shared_operations = True
        self.reader.save(update_fields=("shared_operations",))

        result = synchronize_start_command(
            command=self.command(
                operation="dispatch",
                picking="EXWS1/OUT/00001",
            )
        )

        self.assertEqual(
            result.session.operation_type,
            RFIDSession.OperationType.DISPATCH,
        )

    def test_shared_reader_still_allows_only_one_active_session(self):
        self.reader.shared_operations = True
        self.reader.save(update_fields=("shared_operations",))

        synchronize_start_command(
            command=self.command(
                session_key="receipt-session",
                operation="receipt",
            )
        )

        with self.assertRaises(FinalSessionError):
            synchronize_start_command(
                command=self.command(
                    session_key="dispatch-session",
                    operation="dispatch",
                    picking="EXWS1/OUT/00001",
                )
            )

    def test_shared_dispatch_session_accepts_events(self):
        self.reader.shared_operations = True
        self.reader.save(update_fields=("shared_operations",))

        session = synchronize_start_command(
            command=self.command(
                operation="dispatch",
                picking="EXWS1/OUT/00001",
            )
        ).session

        event = RawRFIDEvent.objects.create(
            device=self.reader,
            reader_event_key="shared-event-001",
            epc="E28011B0A503007D6BAF049D",
            raw_payload="test",
        )

        result = assign_event_to_active_session(
            event_id=event.event_id,
        )

        event.refresh_from_db()

        self.assertEqual(
            result.session_id,
            session.session_id,
        )
        self.assertEqual(
            event.rfid_session_id,
            session.id,
        )

    def test_dedicated_reader_assignment_guard_remains_intact(self):
        session = RFIDSession.objects.create(
            external_session_key="bad-dispatch-session",
            device=self.reader,
            operation_type=RFIDSession.OperationType.DISPATCH,
            odoo_model="stock.picking",
            odoo_record_id=0,
            odoo_reference="EXWS1/OUT/00001",
            status=RFIDSession.Status.ACTIVE,
        )

        event = RawRFIDEvent.objects.create(
            device=self.reader,
            reader_event_key="guard-event-001",
            epc="TEST",
            raw_payload="test",
        )

        with self.assertRaises(ValueError):
            assign_event_to_active_session(
                event_id=event.event_id,
            )

        session.refresh_from_db()
        self.assertEqual(
            session.status,
            RFIDSession.Status.ACTIVE,
        )


class SharedReaderExplicitOperationReuseTests(TestCase):
    def setUp(self):
        self.reader = ReaderDevice.objects.create(
            code="shared-reuse-reader",
            name="Shared Reuse Reader",
            role=ReaderDevice.Role.RECEIVING,
            host="192.0.2.10",
            port=8090,
            device_address=2,
            enabled=True,
            shared_operations=True,
        )

    def command(self, *, operation):
        return OdooRFIDCommand.from_payload(
            {
                "session_key": "shared-reuse-session",
                "reader_code": self.reader.code,
                "command": "start",
                "revision": 1,
                "picking": (
                    "EXWS1/OUT/00001"
                    if operation == "dispatch"
                    else "EXWS1/IN/00001"
                ),
                "operation": operation,
            }
        )

    def test_same_explicit_operation_reuses_existing_session(self):
        first = synchronize_start_command(
            command=self.command(operation="dispatch")
        )

        second = synchronize_start_command(
            command=self.command(operation="dispatch")
        )

        self.assertTrue(first.created)
        self.assertFalse(first.reused)

        self.assertFalse(second.created)
        self.assertTrue(second.reused)

        self.assertEqual(
            first.session.id,
            second.session.id,
        )
        self.assertEqual(
            second.session.operation_type,
            RFIDSession.OperationType.DISPATCH,
        )

    def test_changed_operation_for_same_session_is_rejected(self):
        synchronize_start_command(
            command=self.command(operation="receipt")
        )

        with self.assertRaises(FinalSessionError):
            synchronize_start_command(
                command=self.command(operation="dispatch")
            )
