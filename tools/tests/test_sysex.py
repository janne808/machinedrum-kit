import os
import unittest

import helpers  # noqa: F401
from mdkit import sysex


class Sysex(unittest.TestCase):
    def test_roundtrip_lengths(self):
        for n in (1, 2, 63, 64, 65, 1001, 64 * 50):
            p = os.urandom(n)
            self.assertEqual(sysex.decode_sysex(sysex.encode_payload(p))[0], p)

    def test_packet_shape(self):
        s = sysex.encode_payload(bytes(64))
        first = s[:s.index(b'\xf7') + 1]
        self.assertEqual(len(first), 112)
        self.assertEqual(first[:7], b'\xf0\x00\x20\x3c\x02\x00\x7e')
        self.assertEqual(first[9:15], bytes([0, 0, 4, 0, 0, 0]))     # address 0x004000

    def test_rejects_corruption(self):
        s = bytearray(sysex.encode_payload(os.urandom(200)))
        s[20] ^= 1
        with self.assertRaises(sysex.SysexError):
            sysex.decode_sysex(bytes(s))

    def test_rejects_reorder_and_trailing(self):
        s = sysex.encode_payload(os.urandom(200))
        msgs = [m + b'\xf7' for m in s.split(b'\xf7') if m]
        with self.assertRaises(sysex.SysexError):
            sysex.decode_sysex(b''.join([msgs[1], msgs[0]] + msgs[2:]))
        with self.assertRaises(sysex.SysexError):
            sysex.decode_sysex(s + msgs[0])

    def test_apply_payload(self):
        base = bytes(sysex.IMAGE_SIZE)
        p = os.urandom(100)
        out = sysex.apply_payload(p, base)
        self.assertEqual(out[0x4000:0x4064], p)
        self.assertEqual(out[:0x4000], base[:0x4000])


if __name__ == '__main__':
    unittest.main()
