import struct
from unittest.mock import Mock

from django.test import SimpleTestCase

from bridge_core.reader_client import (
    ActiveInventoryReaderClient,
    RFIDReaderClientError,
)
from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    COMMAND_START_INVENTORY,
    COMMAND_STOP,
    build_frame,
    parse_frame,
)


def active_tag_payload(epc_hex, *, count=1, antenna=1):
    epc = bytes.fromhex(epc_hex)
    mask = (
        (1 << 0)
        | (1 << 1)
        | (1 << 2)
        | (1 << 4)
        | (1 << 5)
        | (1 << 8)
    )

    return (
        struct.pack(">IB", mask, 1)
        + bytes((antenna,))
        + struct.pack(">H", 0x3000)
        + bytes((len(epc),))
        + epc
        + struct.pack(">i", count)
        + b"\x00" * 12
    )


class ActiveInventoryReaderClientTests(SimpleTestCase):
    def test_collects_and_deduplicates_active_tag_frames(self):
        session = Mock()

        start_response = parse_frame(
            build_frame(
                address=2,
                sequence=0x81,
                command=COMMAND_START_INVENTORY,
                status=0x99,
            )
        )
        first_tag = parse_frame(
            build_frame(
                address=2,
                sequence=0,
                command=COMMAND_ACTIVE_TAG,
                payload=active_tag_payload(
                    "E2801191A50300631AB2F621",
                    count=1,
                ),
            )
        )
        duplicate_tag = parse_frame(
            build_frame(
                address=2,
                sequence=0,
                command=COMMAND_ACTIVE_TAG,
                payload=active_tag_payload(
                    "E2801191A50300631AB2F621",
                    count=2,
                ),
            )
        )
        second_tag = parse_frame(
            build_frame(
                address=2,
                sequence=0,
                command=COMMAND_ACTIVE_TAG,
                payload=active_tag_payload(
                    "300833B2DDD9014000000000",
                    count=1,
                ),
            )
        )
        stop_response = parse_frame(
            build_frame(
                address=2,
                sequence=0x82,
                command=COMMAND_STOP,
            )
        )

        session.receive.side_effect = (
            (start_response, first_tag),
            (duplicate_tag, second_tag),
            (stop_response,),
        )

        clock = Mock(
            side_effect=(
                100.0,
                100.0,
                100.5,
                101.1,
            )
        )

        client = ActiveInventoryReaderClient(
            address=2,
            scan_seconds=1.0,
            time_function=clock,
        )

        result = client.run(session=session)

        self.assertEqual(result.start_frame.status, 0x99)
        self.assertEqual(result.stop_frame.status, 0)
        self.assertEqual(result.total_tag_frames, 3)
        self.assertEqual(len(result.tags), 2)
        self.assertEqual(
            tuple(tag.epc for tag in result.tags),
            (
                "E2801191A50300631AB2F621",
                "300833B2DDD9014000000000",
            ),
        )

        start_request = parse_frame(
            session.send.call_args_list[0].args[0]
        )
        stop_request = parse_frame(
            session.send.call_args_list[1].args[0]
        )

        self.assertEqual(
            start_request.command,
            COMMAND_START_INVENTORY,
        )
        self.assertEqual(
            start_request.payload,
            struct.pack(">I", 0x00000050),
        )
        self.assertEqual(stop_request.command, COMMAND_STOP)

    def test_normal_success_start_status_is_accepted(self):
        session = Mock()

        session.receive.side_effect = (
            (
                parse_frame(
                    build_frame(
                        address=2,
                        sequence=0x81,
                        command=COMMAND_START_INVENTORY,
                        status=0,
                    )
                ),
            ),
            (
                parse_frame(
                    build_frame(
                        address=2,
                        sequence=0x82,
                        command=COMMAND_STOP,
                        status=0,
                    )
                ),
            ),
        )

        clock = Mock(side_effect=(10.0, 10.0, 11.1))

        result = ActiveInventoryReaderClient(
            address=2,
            scan_seconds=1.0,
            time_function=clock,
        ).run(session=session)

        self.assertEqual(result.tags, ())
        self.assertEqual(result.total_tag_frames, 0)

    def test_wrong_reader_address_fails_closed_and_sends_stop(self):
        session = Mock()

        session.receive.side_effect = (
            (
                parse_frame(
                    build_frame(
                        address=1,
                        sequence=0,
                        command=COMMAND_ACTIVE_TAG,
                        payload=active_tag_payload(
                            "E2801191A50300631AB2F621"
                        ),
                    )
                ),
            ),
            (
                parse_frame(
                    build_frame(
                        address=2,
                        sequence=0x82,
                        command=COMMAND_STOP,
                    )
                ),
            ),
        )

        clock = Mock(side_effect=(1.0, 1.0))

        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "address does not match",
        ):
            ActiveInventoryReaderClient(
                address=2,
                scan_seconds=1.0,
                time_function=clock,
            ).run(session=session)

        self.assertEqual(session.send.call_count, 2)

    def test_unsupported_start_status_fails_closed(self):
        session = Mock()

        session.receive.side_effect = (
            (
                parse_frame(
                    build_frame(
                        address=2,
                        sequence=0x81,
                        command=COMMAND_START_INVENTORY,
                        status=5,
                    )
                ),
            ),
            (
                parse_frame(
                    build_frame(
                        address=2,
                        sequence=0x82,
                        command=COMMAND_STOP,
                    )
                ),
            ),
        )

        clock = Mock(side_effect=(1.0, 1.0))

        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "unsupported active inventory start status 5",
        ):
            ActiveInventoryReaderClient(
                address=2,
                scan_seconds=1.0,
                time_function=clock,
            ).run(session=session)

        self.assertEqual(session.send.call_count, 2)

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "address must be between",
        ):
            ActiveInventoryReaderClient(address=256)

        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "greater than 0",
        ):
            ActiveInventoryReaderClient(
                address=2,
                scan_seconds=0,
            )

        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "no more than 600",
        ):
            ActiveInventoryReaderClient(
                address=2,
                scan_seconds=601,
            )
