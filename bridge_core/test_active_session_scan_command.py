from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from bridge_core.models import (
    OperationalConfiguration,
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)
from bridge_core.reader_backends import TechnicalRFIDRead


@override_settings(
    ALLOW_PHYSICAL_READER_CONTACT=False,
    ALLOW_ODOO_CONTACT=False,
    SENDER_BACKEND="disabled",
)
class ActiveRFIDSessionScanCommandTests(TestCase):
    def setUp(self):
        self.configuration = OperationalConfiguration.objects.get(
            name="default"
        )
        self.configuration.poc_allow_physical_reader_contact = True
        self.configuration.poc_allow_odoo_contact = False
        self.configuration.odoo_integration_enabled = False
        self.configuration.save(
            update_fields=(
                "poc_allow_physical_reader_contact",
                "poc_allow_odoo_contact",
                "odoo_integration_enabled",
                "updated_at",
            )
        )
        self.reader = ReaderDevice.objects.create(
            code="door1",
            name="Door1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            inventory_mode=ReaderDevice.InventoryMode.CACHED,
            connect_timeout_seconds=5,
            read_timeout_seconds=5,
            reconnect_delay_seconds=5,
            enabled=True,
        )
        self.session = RFIDSession.objects.create(
            external_session_key="poc-session-001",
            device=self.reader,
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_model="stock.picking",
            odoo_record_id=1001,
            odoo_reference="WH/IN/001",
            status=RFIDSession.Status.ACTIVE,
        )

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_dry_run_does_not_contact_reader_or_store_events(
        self,
        backend_selector,
    ):
        output = StringIO()

        call_command(
            "run_active_rfid_session_scan",
            device_code="door1",
            scan_seconds=10,
            stdout=output,
        )

        backend_selector.assert_not_called()
        self.assertEqual(RawRFIDEvent.objects.count(), 0)
        self.assertIn("MODE=dry-run", output.getvalue())
        self.assertIn(
            "the reader was not contacted",
            output.getvalue(),
        )

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_apply_stores_and_assigns_unique_read(
        self,
        backend_selector,
    ):
        backend = Mock()
        backend.read_events.return_value = (
            TechnicalRFIDRead(
                reader_event_key="active:test-scan:tag-1",
                epc="E2801191A50300631AB2F621",
                raw_payload='{"source":"active_inventory"}',
            ),
        )
        backend_selector.return_value = backend

        output = StringIO()

        call_command(
            "run_active_rfid_session_scan",
            device_code="door1",
            scan_seconds=10,
            confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
            apply=True,
            stdout=output,
        )

        event = RawRFIDEvent.objects.get()

        self.assertEqual(
            event.epc,
            "E2801191A50300631AB2F621",
        )
        self.assertEqual(event.rfid_session, self.session)
        self.assertEqual(
            event.queue_state,
            RawRFIDEvent.QueueState.QUEUED,
        )
        self.assertIn("CREATED_EVENTS=1", output.getvalue())
        self.assertIn("ASSIGNED_EVENTS=1", output.getvalue())
        self.assertIn("ODOO_CONTACT=NO", output.getvalue())

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_missing_active_session_blocks_before_reader_contact(
        self,
        backend_selector,
    ):
        self.session.delete()

        with self.assertRaisesMessage(
            CommandError,
            "No active RFID session exists",
        ):
            call_command(
                "run_active_rfid_session_scan",
                device_code="door1",
                confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
                apply=True,
            )

        backend_selector.assert_not_called()
        self.assertEqual(RawRFIDEvent.objects.count(), 0)

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_incorrect_confirmation_blocks_before_contact(
        self,
        backend_selector,
    ):
        with self.assertRaisesMessage(
            CommandError,
            "exact confirmation phrase",
        ):
            call_command(
                "run_active_rfid_session_scan",
                device_code="door1",
                confirmation="WRONG",
                apply=True,
            )

        backend_selector.assert_not_called()
        self.assertEqual(RawRFIDEvent.objects.count(), 0)

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_odoo_contact_permission_blocks_reader_scan(
        self,
        backend_selector,
    ):
        self.configuration.poc_allow_odoo_contact = True
        self.configuration.save(
            update_fields=(
                "poc_allow_odoo_contact",
                "updated_at",
            )
        )

        with self.assertRaisesMessage(
            CommandError,
            "Odoo contact must remain disabled",
        ):
            call_command(
                "run_active_rfid_session_scan",
                device_code="door1",
                confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
                apply=True,
            )

        backend_selector.assert_not_called()
        self.assertEqual(RawRFIDEvent.objects.count(), 0)

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_incompatible_session_blocks_before_contact(
        self,
        backend_selector,
    ):
        self.session.operation_type = RFIDSession.OperationType.DISPATCH
        self.session.save(
            update_fields=(
                "operation_type",
                "updated_at",
            )
        )

        with self.assertRaisesMessage(
            CommandError,
            "incompatible with the reader role",
        ):
            call_command(
                "run_active_rfid_session_scan",
                device_code="door1",
                confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
                apply=True,
            )

        backend_selector.assert_not_called()


class ActiveRFIDSessionCrossScanDeduplicationTests(
    ActiveRFIDSessionScanCommandTests
):
    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_existing_session_epc_is_not_created_again(
        self,
        backend_selector,
    ):
        RawRFIDEvent.objects.create(
            device=self.reader,
            rfid_session=self.session,
            reader_event_key="active:previous-scan:existing-tag",
            epc="E2801191A50300631AB2F621",
            raw_payload='{"source":"active_inventory"}',
            queue_state=RawRFIDEvent.QueueState.QUEUED,
        )

        backend = Mock()
        backend.read_events.return_value = (
            TechnicalRFIDRead(
                reader_event_key="active:new-scan:existing-tag",
                epc="E2801191A50300631AB2F621",
                raw_payload='{"source":"active_inventory"}',
            ),
        )
        backend_selector.return_value = backend

        output = StringIO()

        call_command(
            "run_active_rfid_session_scan",
            device_code="door1",
            scan_seconds=10,
            confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
            apply=True,
            stdout=output,
        )

        self.assertEqual(
            RawRFIDEvent.objects.filter(
                rfid_session=self.session,
                epc="E2801191A50300631AB2F621",
            ).count(),
            1,
        )
        self.assertIn(
            "ALREADY_IN_SESSION=1",
            output.getvalue(),
        )
        self.assertIn(
            "NEW_TECHNICAL_READS=0",
            output.getvalue(),
        )
        self.assertIn(
            "CREATED_EVENTS=0",
            output.getvalue(),
        )
        self.assertIn(
            "ASSIGNED_EVENTS=0",
            output.getvalue(),
        )

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_existing_and_new_epcs_only_create_new_epc(
        self,
        backend_selector,
    ):
        RawRFIDEvent.objects.create(
            device=self.reader,
            rfid_session=self.session,
            reader_event_key="active:previous-scan:existing-tag",
            epc="E2801191A50300631AB2F621",
            raw_payload='{"source":"active_inventory"}',
            queue_state=RawRFIDEvent.QueueState.QUEUED,
        )

        backend = Mock()
        backend.read_events.return_value = (
            TechnicalRFIDRead(
                reader_event_key="active:new-scan:existing-tag",
                epc="e2801191a50300631ab2f621",
                raw_payload='{"source":"active_inventory"}',
            ),
            TechnicalRFIDRead(
                reader_event_key="active:new-scan:new-tag",
                epc="E2801191A50300631AB2F622",
                raw_payload='{"source":"active_inventory"}',
            ),
        )
        backend_selector.return_value = backend

        output = StringIO()

        call_command(
            "run_active_rfid_session_scan",
            device_code="door1",
            scan_seconds=10,
            confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
            apply=True,
            stdout=output,
        )

        self.assertEqual(
            RawRFIDEvent.objects.filter(
                rfid_session=self.session,
            ).count(),
            2,
        )
        self.assertEqual(
            RawRFIDEvent.objects.filter(
                rfid_session=self.session,
                epc="E2801191A50300631AB2F621",
            ).count(),
            1,
        )
        self.assertEqual(
            RawRFIDEvent.objects.filter(
                rfid_session=self.session,
                epc="E2801191A50300631AB2F622",
            ).count(),
            1,
        )
        self.assertIn(
            "TECHNICAL_READS=2",
            output.getvalue(),
        )
        self.assertIn(
            "ALREADY_IN_SESSION=1",
            output.getvalue(),
        )
        self.assertIn(
            "NEW_TECHNICAL_READS=1",
            output.getvalue(),
        )
        self.assertIn(
            "CREATED_EVENTS=1",
            output.getvalue(),
        )
        self.assertIn(
            "ASSIGNED_EVENTS=1",
            output.getvalue(),
        )

    @patch(
        "bridge_core.management.commands."
        "run_active_rfid_session_scan.get_reader_backend"
    )
    def test_same_epc_in_different_session_is_allowed(
        self,
        backend_selector,
    ):
        previous_session = RFIDSession.objects.create(
            external_session_key="previous-session-001",
            device=ReaderDevice.objects.create(
                code="door2",
                name="Door2",
                role=ReaderDevice.Role.RECEIVING,
                host="192.168.1.202",
                port=8090,
                device_address=3,
                inventory_mode=ReaderDevice.InventoryMode.CACHED,
                enabled=True,
            ),
            operation_type=RFIDSession.OperationType.RECEIPT,
            odoo_model="stock.picking",
            odoo_record_id=1002,
            odoo_reference="WH/IN/002",
            status=RFIDSession.Status.CLOSED,
        )

        RawRFIDEvent.objects.create(
            device=previous_session.device,
            rfid_session=previous_session,
            reader_event_key="active:previous-session:tag",
            epc="E2801191A50300631AB2F621",
            raw_payload='{"source":"active_inventory"}',
            queue_state=RawRFIDEvent.QueueState.QUEUED,
        )

        backend = Mock()
        backend.read_events.return_value = (
            TechnicalRFIDRead(
                reader_event_key="active:current-session:tag",
                epc="E2801191A50300631AB2F621",
                raw_payload='{"source":"active_inventory"}',
            ),
        )
        backend_selector.return_value = backend

        call_command(
            "run_active_rfid_session_scan",
            device_code="door1",
            scan_seconds=10,
            confirmation="SCAN_AND_STORE_ACTIVE_SESSION",
            apply=True,
        )

        self.assertEqual(
            RawRFIDEvent.objects.filter(
                rfid_session=self.session,
                epc="E2801191A50300631AB2F621",
            ).count(),
            1,
        )
