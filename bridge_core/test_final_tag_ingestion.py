import struct

from django.test import TestCase

from bridge_core.final_tag_ingestion import (
    FinalTagIngestionError,
    ingest_final_active_tag_frames,
)
from bridge_core.models import (
    RawRFIDEvent,
    ReaderDevice,
    RFIDSession,
)
from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    COMMAND_STOP,
    RFIDFrame,
)


def tag_payload(epc):
    epc_bytes = bytes.fromhex(epc)

    # mask:
    # bit 2 = EPC
    mask = 1 << 2
    protocol_type = 1

    return (
        struct.pack(
            ">IB",
            mask,
            protocol_type,
        )
        + bytes((len(epc_bytes),))
        + epc_bytes
    )


def active_tag_frame(
    epc,
    *,
    address=2,
    status=0,
    sequence=1,
):
    return RFIDFrame(
        address=address,
        sequence=sequence,
        command=COMMAND_ACTIVE_TAG,
        status=status,
        payload=tag_payload(epc),
        raw_frame=b"",
    )


class FinalTagIngestionTests(TestCase):
    def setUp(self):
        self.reader = ReaderDevice.objects.create(
            code="receiving-door-01",
            name="Receiving Door 1",
            role=ReaderDevice.Role.RECEIVING,
            host="192.168.1.201",
            port=8090,
            device_address=2,
            inventory_mode=(
                ReaderDevice.InventoryMode.ACTIVE
            ),
            enabled=True,
        )

        self.session = RFIDSession.objects.create(
            external_session_key="session-001",
            device=self.reader,
            operation_type=(
                RFIDSession.OperationType.RECEIPT
            ),
            odoo_model="stock.picking",
            odoo_record_id=0,
            odoo_reference="EXWS1/IN/02227",
            status=RFIDSession.Status.ACTIVE,
        )

    def test_one_tag_is_persisted_and_queued(self):
        result = ingest_final_active_tag_frames(
            device=self.reader,
            session_key="session-001",
            frames=(
                active_tag_frame(
                    "300833B2DDD9014000000001"
                ),
            ),
        )

        self.assertEqual(
            result.created_count,
            1,
        )
        self.assertEqual(
            result.assigned_count,
            1,
        )

        event = RawRFIDEvent.objects.get()

        self.assertEqual(
            event.epc,
            "300833B2DDD9014000000001",
        )
        self.assertEqual(
            event.rfid_session,
            self.session,
        )
        self.assertEqual(
            event.queue_state,
            RawRFIDEvent.QueueState.QUEUED,
        )

    def test_same_epc_twice_in_batch_creates_once(self):
        epc = "300833B2DDD9014000000001"

        result = ingest_final_active_tag_frames(
            device=self.reader,
            session_key="session-001",
            frames=(
                active_tag_frame(
                    epc,
                    sequence=1,
                ),
                active_tag_frame(
                    epc,
                    sequence=2,
                ),
            ),
        )

        self.assertEqual(
            result.frame_count,
            2,
        )
        self.assertEqual(
            result.created_count,
            1,
        )
        self.assertEqual(
            result.duplicate_count,
            1,
        )
        self.assertEqual(
            RawRFIDEvent.objects.count(),
            1,
        )

    def test_same_epc_across_polls_creates_once(self):
        epc = "300833B2DDD9014000000001"

        first = ingest_final_active_tag_frames(
            device=self.reader,
            session_key="session-001",
            frames=(
                active_tag_frame(epc),
            ),
        )

        second = ingest_final_active_tag_frames(
            device=self.reader,
            session_key="session-001",
            frames=(
                active_tag_frame(
                    epc,
                    sequence=9,
                ),
            ),
        )

        self.assertEqual(
            first.created_count,
            1,
        )
        self.assertEqual(
            second.created_count,
            0,
        )
        self.assertEqual(
            second.duplicate_count,
            1,
        )
        self.assertEqual(
            RawRFIDEvent.objects.count(),
            1,
        )

    def test_two_different_epcs_create_two_events(self):
        result = ingest_final_active_tag_frames(
            device=self.reader,
            session_key="session-001",
            frames=(
                active_tag_frame(
                    "300833B2DDD9014000000001"
                ),
                active_tag_frame(
                    "300833B2DDD9014000000002"
                ),
            ),
        )

        self.assertEqual(
            result.created_count,
            2,
        )
        self.assertEqual(
            RawRFIDEvent.objects.count(),
            2,
        )

    def test_wrong_session_key_is_rejected(self):
        with self.assertRaises(
            FinalTagIngestionError
        ):
            ingest_final_active_tag_frames(
                device=self.reader,
                session_key="wrong-session",
                frames=(
                    active_tag_frame(
                        "300833B2DDD9014000000001"
                    ),
                ),
            )

        self.assertEqual(
            RawRFIDEvent.objects.count(),
            0,
        )

    def test_closed_session_is_rejected(self):
        self.session.status = (
            RFIDSession.Status.CLOSED
        )
        self.session.save(
            update_fields=("status",)
        )

        with self.assertRaises(
            FinalTagIngestionError
        ):
            ingest_final_active_tag_frames(
                device=self.reader,
                session_key="session-001",
                frames=(
                    active_tag_frame(
                        "300833B2DDD9014000000001"
                    ),
                ),
            )

    def test_wrong_reader_address_is_rejected(self):
        with self.assertRaises(
            FinalTagIngestionError
        ):
            ingest_final_active_tag_frames(
                device=self.reader,
                session_key="session-001",
                frames=(
                    active_tag_frame(
                        "300833B2DDD9014000000001",
                        address=3,
                    ),
                ),
            )

    def test_failed_tag_status_is_rejected(self):
        with self.assertRaises(
            FinalTagIngestionError
        ):
            ingest_final_active_tag_frames(
                device=self.reader,
                session_key="session-001",
                frames=(
                    active_tag_frame(
                        "300833B2DDD9014000000001",
                        status=5,
                    ),
                ),
            )

    def test_non_tag_command_is_rejected(self):
        bad = RFIDFrame(
            address=2,
            sequence=1,
            command=COMMAND_STOP,
            status=0,
            payload=b"",
            raw_frame=b"",
        )

        with self.assertRaises(
            FinalTagIngestionError
        ):
            ingest_final_active_tag_frames(
                device=self.reader,
                session_key="session-001",
                frames=(bad,),
            )

    def test_empty_batch_is_safe(self):
        result = ingest_final_active_tag_frames(
            device=self.reader,
            session_key="session-001",
            frames=(),
        )

        self.assertEqual(
            result.created_count,
            0,
        )
        self.assertEqual(
            RawRFIDEvent.objects.count(),
            0,
        )
