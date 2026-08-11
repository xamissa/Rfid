import struct

from django.test import SimpleTestCase

from bridge_core.reader_protocol import (
    COMMAND_ACTIVE_TAG,
    COMMAND_HANDSHAKE,
    COMMAND_INVENTORY_STATISTICS,
    FrameStreamDecoder,
    RFIDProtocolError,
    build_frame,
    calculate_crc,
    parse_frame,
    parse_inventory_statistics,
    parse_tag_data,
)


class RFIDProtocolTests(SimpleTestCase):
    def test_crc_matches_standard_ccitt_false_vector(self):
        self.assertEqual(
            calculate_crc(b"123456789"),
            0x29B1,
        )

    def test_handshake_frame_matches_proven_wire_format(self):
        frame = build_frame(
            address=1,
            sequence=1,
            command=COMMAND_HANDSHAKE,
            payload=struct.pack(">I", 1),
        )

        self.assertEqual(
            frame.hex().upper(),
            "FE000D010100000000000104ED",
        )

        parsed = parse_frame(frame)

        self.assertEqual(parsed.address, 1)
        self.assertEqual(parsed.sequence, 1)
        self.assertEqual(parsed.command, COMMAND_HANDSHAKE)
        self.assertEqual(parsed.status, 0)
        self.assertEqual(parsed.payload, bytes.fromhex("00000001"))
        self.assertEqual(parsed.raw_frame, frame)

    def test_invalid_crc_is_rejected(self):
        frame = bytearray(
            build_frame(
                address=1,
                sequence=2,
                command=COMMAND_HANDSHAKE,
            )
        )
        frame[-1] ^= 0x01

        with self.assertRaisesMessage(
            RFIDProtocolError,
            "CRC validation failed",
        ):
            parse_frame(bytes(frame))

    def test_declared_length_mismatch_is_rejected(self):
        frame = build_frame(
            address=1,
            sequence=3,
            command=COMMAND_HANDSHAKE,
            payload=bytes((1,)),
        )

        self.assertEqual(len(frame), 10)
        self.assertEqual(len(frame[:-1]), 9)

        with self.assertRaisesMessage(
            RFIDProtocolError,
            "does not match received bytes",
        ):
            parse_frame(frame[:-1])

    def test_inventory_statistics_are_big_endian_integers(self):
        statistics = parse_inventory_statistics(
            struct.pack(">iii", 2500, 42, 7)
        )

        self.assertEqual(statistics.duration_ms, 2500)
        self.assertEqual(statistics.read_count, 42)
        self.assertEqual(statistics.tag_count, 7)

    def test_tag_payload_matches_bhumika_mask_order(self):
        mask = 0b11111
        payload = (
            struct.pack(">IB", mask, 1)
            + bytes((2,))
            + struct.pack(">h", -32768)
            + bytes((12,))
            + bytes.fromhex("E2000017221101441890ABCD")
            + bytes((6,))
            + bytes.fromhex("E28011606000")
            + struct.pack(">i", 9)
        )

        tag = parse_tag_data(payload)

        self.assertEqual(tag.protocol_type, 1)
        self.assertEqual(tag.antenna, 2)
        self.assertEqual(tag.pc, -32768)
        self.assertEqual(
            tag.epc,
            "E2000017221101441890ABCD",
        )
        self.assertEqual(tag.tid, "E28011606000")
        self.assertEqual(tag.count, 9)

    def test_truncated_variable_tag_field_is_rejected(self):
        payload = (
            struct.pack(">IB", 1 << 2, 1)
            + bytes((12,))
            + bytes.fromhex("E200")
        )

        with self.assertRaisesMessage(
            RFIDProtocolError,
            "ended while reading EPC",
        ):
            parse_tag_data(payload)

    def test_stream_decoder_handles_fragmented_frame(self):
        frame = build_frame(
            address=1,
            sequence=4,
            command=COMMAND_ACTIVE_TAG,
            payload=struct.pack(">IB", 0, 1),
        )
        decoder = FrameStreamDecoder()

        first = decoder.feed(frame[:4])
        second = decoder.feed(frame[4:])

        self.assertEqual(first, ())
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].raw_frame, frame)
        self.assertEqual(decoder.pending_bytes, b"")

    def test_stream_decoder_handles_noise_and_coalesced_frames(self):
        first_frame = build_frame(
            address=1,
            sequence=5,
            command=COMMAND_HANDSHAKE,
        )
        second_frame = build_frame(
            address=1,
            sequence=6,
            command=COMMAND_INVENTORY_STATISTICS,
            payload=struct.pack(">iii", 100, 2, 1),
        )
        decoder = FrameStreamDecoder()

        frames = decoder.feed(
            b"noise" + first_frame + second_frame
        )

        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].sequence, 5)
        self.assertEqual(frames[1].sequence, 6)
        self.assertEqual(decoder.pending_bytes, b"")

    def test_stream_decoder_recovers_after_corrupt_frame(self):
        corrupt = bytearray(
            build_frame(
                address=1,
                sequence=7,
                command=COMMAND_HANDSHAKE,
            )
        )
        corrupt[-1] ^= 0x01

        valid = build_frame(
            address=1,
            sequence=8,
            command=COMMAND_HANDSHAKE,
        )

        decoder = FrameStreamDecoder()
        frames = decoder.feed(bytes(corrupt) + valid)

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].sequence, 8)
        self.assertEqual(decoder.pending_bytes, b"")
