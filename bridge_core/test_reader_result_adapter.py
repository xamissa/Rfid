import json

from django.test import SimpleTestCase

from bridge_core.reader_client import CachedInventoryResult
from bridge_core.reader_protocol import (
    RFIDFrame,
    RFIDTagData,
)
from bridge_core.reader_result_adapter import (
    RFIDResultAdapterError,
    cached_inventory_to_technical_reads,
)


def make_frame():
    return RFIDFrame(
        address=1,
        sequence=11,
        command=0xF8,
        status=0,
        payload=b"",
        raw_frame=b"",
    )


def make_tag(
    *,
    epc,
    antenna=1,
    pc=12288,
    tid="E2801105200065622A7B1234",
    count=3,
):
    return RFIDTagData(
        protocol_type=0,
        antenna=antenna,
        pc=pc,
        epc=epc,
        tid=tid,
        count=count,
    )


class CachedInventoryResultAdapterTests(SimpleTestCase):
    def test_converts_each_cached_tag_to_technical_read(self):
        result = CachedInventoryResult(
            statistics_frame=make_frame(),
            duration_ms=3000,
            read_count=7,
            expected_tag_count=2,
            tags=(
                make_tag(
                    epc="e2000017221101441890abcd",
                    count=4,
                ),
                make_tag(
                    epc="300833B2DDD9014000000001",
                    antenna=2,
                    count=3,
                ),
            ),
        )

        reads = cached_inventory_to_technical_reads(
            result=result,
            scan_id="scan-20260721-001",
        )

        self.assertEqual(len(reads), 2)
        self.assertEqual(
            reads[0].epc,
            "E2000017221101441890ABCD",
        )
        self.assertEqual(
            reads[1].epc,
            "300833B2DDD9014000000001",
        )

        first_payload = json.loads(
            reads[0].raw_payload
        )

        self.assertEqual(
            first_payload["source"],
            "cached_inventory",
        )
        self.assertEqual(
            first_payload["scan_id"],
            "scan-20260721-001",
        )
        self.assertEqual(
            first_payload["row_index"],
            0,
        )
        self.assertEqual(
            first_payload["statistics"],
            {
                "duration_ms": 3000,
                "expected_tag_count": 2,
                "read_count": 7,
            },
        )
        self.assertEqual(
            first_payload["tag"]["protocol_type"],
            0,
        )
        self.assertEqual(
            first_payload["tag"]["antenna"],
            1,
        )
        self.assertEqual(
            first_payload["tag"]["count"],
            4,
        )
        self.assertEqual(
            first_payload["tag"]["tid"],
            "E2801105200065622A7B1234",
        )

    def test_same_scan_produces_deterministic_event_keys(self):
        result = CachedInventoryResult(
            statistics_frame=make_frame(),
            duration_ms=1000,
            read_count=1,
            expected_tag_count=1,
            tags=(
                make_tag(
                    epc="E2000017221101441890ABCD"
                ),
            ),
        )

        first = cached_inventory_to_technical_reads(
            result=result,
            scan_id="stable-scan-id",
        )
        second = cached_inventory_to_technical_reads(
            result=result,
            scan_id="stable-scan-id",
        )

        self.assertEqual(
            first[0].reader_event_key,
            second[0].reader_event_key,
        )
        self.assertLessEqual(
            len(first[0].reader_event_key),
            128,
        )

    def test_different_scan_ids_produce_different_keys(self):
        result = CachedInventoryResult(
            statistics_frame=make_frame(),
            duration_ms=1000,
            read_count=1,
            expected_tag_count=1,
            tags=(
                make_tag(
                    epc="E2000017221101441890ABCD"
                ),
            ),
        )

        first = cached_inventory_to_technical_reads(
            result=result,
            scan_id="scan-one",
        )
        second = cached_inventory_to_technical_reads(
            result=result,
            scan_id="scan-two",
        )

        self.assertNotEqual(
            first[0].reader_event_key,
            second[0].reader_event_key,
        )

    def test_row_index_keeps_duplicate_epcs_distinct(self):
        tag = make_tag(
            epc="E2000017221101441890ABCD"
        )
        result = CachedInventoryResult(
            statistics_frame=make_frame(),
            duration_ms=1000,
            read_count=2,
            expected_tag_count=2,
            tags=(tag, tag),
        )

        reads = cached_inventory_to_technical_reads(
            result=result,
            scan_id="duplicate-epc-scan",
        )

        self.assertNotEqual(
            reads[0].reader_event_key,
            reads[1].reader_event_key,
        )

    def test_zero_tag_scan_returns_empty_tuple(self):
        result = CachedInventoryResult(
            statistics_frame=make_frame(),
            duration_ms=1000,
            read_count=0,
            expected_tag_count=0,
            tags=(),
        )

        reads = cached_inventory_to_technical_reads(
            result=result,
            scan_id="empty-scan",
        )

        self.assertEqual(reads, ())

    def test_empty_scan_id_is_rejected(self):
        result = CachedInventoryResult(
            statistics_frame=make_frame(),
            duration_ms=1000,
            read_count=0,
            expected_tag_count=0,
            tags=(),
        )

        with self.assertRaisesMessage(
            RFIDResultAdapterError,
            "Scan ID cannot be empty",
        ):
            cached_inventory_to_technical_reads(
                result=result,
                scan_id="   ",
            )

    def test_mismatched_tag_count_is_rejected(self):
        result = CachedInventoryResult(
            statistics_frame=make_frame(),
            duration_ms=1000,
            read_count=1,
            expected_tag_count=2,
            tags=(
                make_tag(
                    epc="E2000017221101441890ABCD"
                ),
            ),
        )

        with self.assertRaisesMessage(
            RFIDResultAdapterError,
            "tag count does not match",
        ):
            cached_inventory_to_technical_reads(
                result=result,
                scan_id="bad-count-scan",
            )


class ActiveInventoryResultAdapterTests(SimpleTestCase):
    def make_active_result(self):
        from bridge_core.reader_client import ActiveInventoryResult

        start_frame = RFIDFrame(
            address=2,
            sequence=0x81,
            command=0x81,
            status=0x99,
            payload=b"",
            raw_frame=b"",
        )
        stop_frame = RFIDFrame(
            address=2,
            sequence=0x82,
            command=0x7F,
            status=0,
            payload=b"",
            raw_frame=b"",
        )

        return ActiveInventoryResult(
            start_frame=start_frame,
            stop_frame=stop_frame,
            tag_frames=(),
            tags=(
                make_tag(
                    epc="e2801191a50300631ab2f621",
                    count=65,
                ),
                make_tag(
                    epc="300833B2DDD9014000000000",
                    antenna=1,
                    count=20,
                ),
            ),
            total_tag_frames=196,
        )

    def test_converts_unique_active_tags_to_technical_reads(self):
        from bridge_core.reader_result_adapter import (
            active_inventory_to_technical_reads,
        )

        reads = active_inventory_to_technical_reads(
            result=self.make_active_result(),
            scan_id="door1:active-scan-001",
        )

        self.assertEqual(len(reads), 2)
        self.assertEqual(
            reads[0].epc,
            "E2801191A50300631AB2F621",
        )
        self.assertEqual(
            reads[1].epc,
            "300833B2DDD9014000000000",
        )

        payload = json.loads(reads[0].raw_payload)

        self.assertEqual(
            payload["source"],
            "active_inventory",
        )
        self.assertEqual(
            payload["scan_id"],
            "door1:active-scan-001",
        )
        self.assertEqual(
            payload["active_inventory"],
            {
                "start_status": 153,
                "stop_status": 0,
                "total_tag_frames": 196,
                "unique_tag_count": 2,
            },
        )
        self.assertEqual(
            payload["tag"]["count"],
            65,
        )

    def test_active_event_keys_are_deterministic_per_scan_and_epc(self):
        from bridge_core.reader_result_adapter import (
            active_inventory_to_technical_reads,
        )

        result = self.make_active_result()

        first = active_inventory_to_technical_reads(
            result=result,
            scan_id="stable-active-scan",
        )
        second = active_inventory_to_technical_reads(
            result=result,
            scan_id="stable-active-scan",
        )

        self.assertEqual(
            first[0].reader_event_key,
            second[0].reader_event_key,
        )
        self.assertNotEqual(
            first[0].reader_event_key,
            first[1].reader_event_key,
        )
        self.assertLessEqual(
            len(first[0].reader_event_key),
            128,
        )

    def test_different_active_scans_produce_different_keys(self):
        from bridge_core.reader_result_adapter import (
            active_inventory_to_technical_reads,
        )

        result = self.make_active_result()

        first = active_inventory_to_technical_reads(
            result=result,
            scan_id="active-scan-one",
        )
        second = active_inventory_to_technical_reads(
            result=result,
            scan_id="active-scan-two",
        )

        self.assertNotEqual(
            first[0].reader_event_key,
            second[0].reader_event_key,
        )

    def test_empty_active_scan_returns_empty_tuple(self):
        from bridge_core.reader_client import ActiveInventoryResult
        from bridge_core.reader_result_adapter import (
            active_inventory_to_technical_reads,
        )

        frame = RFIDFrame(
            address=2,
            sequence=1,
            command=0x81,
            status=0,
            payload=b"",
            raw_frame=b"",
        )

        result = ActiveInventoryResult(
            start_frame=frame,
            stop_frame=frame,
            tag_frames=(),
            tags=(),
            total_tag_frames=0,
        )

        reads = active_inventory_to_technical_reads(
            result=result,
            scan_id="empty-active-scan",
        )

        self.assertEqual(reads, ())
