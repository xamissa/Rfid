import struct
from unittest.mock import Mock, call

from django.test import SimpleTestCase

from bridge_core.reader_client import (
    CachedInventoryReaderClient,
    RFIDReaderClientError,
)
from bridge_core.reader_protocol import (
    COMMAND_CACHE_TAG,
    COMMAND_HANDSHAKE,
    COMMAND_INVENTORY_STATISTICS,
    COMMAND_START_INVENTORY,
    COMMAND_STOP,
    build_frame,
    parse_frame,
)


def tag_payload(epc_hex, *, count=1):
    epc = bytes.fromhex(epc_hex)
    mask = (1 << 2) | (1 << 4)

    return (
        struct.pack(">IB", mask, 1)
        + bytes((len(epc),))
        + epc
        + struct.pack(">i", count)
    )


class CachedInventoryReaderClientTests(SimpleTestCase):
    def test_cached_inventory_matches_bhumika_command_flow(self):
        session = Mock()
        sleeper = Mock()

        handshake_response = parse_frame(
            build_frame(
                address=1,
                sequence=1,
                command=COMMAND_HANDSHAKE,
            )
        )
        statistics_response = parse_frame(
            build_frame(
                address=1,
                sequence=11,
                command=COMMAND_INVENTORY_STATISTICS,
                payload=struct.pack(">iii", 3000, 7, 2),
            )
        )
        first_tag_response = parse_frame(
            build_frame(
                address=1,
                sequence=20,
                command=COMMAND_CACHE_TAG,
                payload=tag_payload(
                    "E2000017221101441890ABCD",
                    count=4,
                ),
            )
        )
        second_tag_response = parse_frame(
            build_frame(
                address=1,
                sequence=21,
                command=COMMAND_CACHE_TAG,
                payload=tag_payload(
                    "300833B2DDD9014000000001",
                    count=3,
                ),
            )
        )

        session.exchange.side_effect = (
            (handshake_response,),
            (first_tag_response,),
            (second_tag_response,),
        )
        session.receive.return_value = (
            statistics_response,
        )

        client = CachedInventoryReaderClient(
            address=1,
            scan_seconds=3.0,
            sleep_function=sleeper,
        )

        result = client.run(session=session)

        sleeper.assert_called_once_with(3.0)

        handshake_outbound = parse_frame(
            session.exchange.call_args_list[0].kwargs[
                "outbound_frame"
            ]
        )
        self.assertEqual(
            handshake_outbound.command,
            COMMAND_HANDSHAKE,
        )
        self.assertEqual(
            handshake_outbound.sequence,
            1,
        )
        self.assertEqual(
            handshake_outbound.payload,
            struct.pack(">I", 1),
        )

        start_outbound = parse_frame(
            session.send.call_args_list[0].args[0]
        )
        stop_outbound = parse_frame(
            session.send.call_args_list[1].args[0]
        )

        self.assertEqual(
            start_outbound.command,
            COMMAND_START_INVENTORY,
        )
        self.assertEqual(start_outbound.sequence, 10)
        self.assertEqual(
            start_outbound.payload,
            struct.pack(">I", 1),
        )

        self.assertEqual(
            stop_outbound.command,
            COMMAND_STOP,
        )
        self.assertEqual(stop_outbound.sequence, 11)
        self.assertEqual(stop_outbound.payload, b"")

        session.receive.assert_called_once_with(
            expected_commands=(
                COMMAND_INVENTORY_STATISTICS,
            ),
        )

        cache_calls = session.exchange.call_args_list[1:]

        self.assertEqual(len(cache_calls), 2)

        first_request = parse_frame(
            cache_calls[0].kwargs["outbound_frame"]
        )
        second_request = parse_frame(
            cache_calls[1].kwargs["outbound_frame"]
        )

        self.assertEqual(first_request.command, COMMAND_CACHE_TAG)
        self.assertEqual(first_request.sequence, 20)
        self.assertEqual(
            first_request.payload,
            struct.pack(">i", 0),
        )

        self.assertEqual(second_request.command, COMMAND_CACHE_TAG)
        self.assertEqual(second_request.sequence, 21)
        self.assertEqual(
            second_request.payload,
            struct.pack(">i", 1),
        )

        self.assertEqual(result.duration_ms, 3000)
        self.assertEqual(result.read_count, 7)
        self.assertEqual(result.expected_tag_count, 2)
        self.assertEqual(len(result.tags), 2)
        self.assertEqual(
            result.tags[0].epc,
            "E2000017221101441890ABCD",
        )
        self.assertEqual(result.tags[0].count, 4)
        self.assertEqual(
            result.tags[1].epc,
            "300833B2DDD9014000000001",
        )
        self.assertEqual(result.tags[1].count, 3)

    def test_zero_tag_statistics_performs_no_cache_requests(self):
        session = Mock()
        sleeper = Mock()

        session.exchange.return_value = (
            parse_frame(
                build_frame(
                    address=1,
                    sequence=1,
                    command=COMMAND_HANDSHAKE,
                )
            ),
        )
        session.receive.return_value = (
            parse_frame(
                build_frame(
                    address=1,
                    sequence=11,
                    command=COMMAND_INVENTORY_STATISTICS,
                    payload=struct.pack(">iii", 1000, 0, 0),
                )
            ),
        )

        client = CachedInventoryReaderClient(
            address=1,
            scan_seconds=0,
            sleep_function=sleeper,
        )

        result = client.run(session=session)

        self.assertEqual(
            session.exchange.call_count,
            1,
        )
        self.assertEqual(result.tags, ())
        self.assertEqual(result.expected_tag_count, 0)

    def test_nonzero_reader_status_fails_closed(self):
        session = Mock()

        session.exchange.return_value = (
            parse_frame(
                build_frame(
                    address=1,
                    sequence=1,
                    command=COMMAND_HANDSHAKE,
                    status=5,
                )
            ),
        )

        client = CachedInventoryReaderClient(
            address=1,
            sleep_function=Mock(),
        )

        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "failure status 5",
        ):
            client.run(session=session)

        session.send.assert_not_called()
        session.receive.assert_not_called()

    def test_response_from_wrong_reader_address_fails_closed(self):
        session = Mock()

        session.exchange.return_value = (
            parse_frame(
                build_frame(
                    address=2,
                    sequence=1,
                    command=COMMAND_HANDSHAKE,
                )
            ),
        )

        client = CachedInventoryReaderClient(
            address=1,
            sleep_function=Mock(),
        )

        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "address does not match",
        ):
            client.run(session=session)

    def test_cache_sequence_wraps_to_one_byte(self):
        session = Mock()

        handshake = parse_frame(
            build_frame(
                address=1,
                sequence=1,
                command=COMMAND_HANDSHAKE,
            )
        )
        statistics = parse_frame(
            build_frame(
                address=1,
                sequence=11,
                command=COMMAND_INVENTORY_STATISTICS,
                payload=struct.pack(">iii", 1, 237, 237),
            )
        )
        cache_response = parse_frame(
            build_frame(
                address=1,
                sequence=0,
                command=COMMAND_CACHE_TAG,
                payload=tag_payload(
                    "E2000017221101441890ABCD"
                ),
            )
        )

        session.exchange.side_effect = (
            [(handshake,)]
            + [(cache_response,)] * 237
        )
        session.receive.return_value = (statistics,)

        client = CachedInventoryReaderClient(
            address=1,
            scan_seconds=0,
            sleep_function=Mock(),
        )

        result = client.run(session=session)

        final_request = parse_frame(
            session.exchange.call_args_list[-1].kwargs[
                "outbound_frame"
            ]
        )

        self.assertEqual(final_request.sequence, 0)
        self.assertEqual(result.expected_tag_count, 237)

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "address must be between",
        ):
            CachedInventoryReaderClient(address=256)

        with self.assertRaisesMessage(
            RFIDReaderClientError,
            "duration must be between",
        ):
            CachedInventoryReaderClient(
                address=1,
                scan_seconds=301,
            )
