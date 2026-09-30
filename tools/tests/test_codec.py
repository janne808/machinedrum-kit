import os
import random
import unittest

import helpers  # noqa: F401  (path setup)
from mdkit import codec


class Codec(unittest.TestCase):
    def roundtrip(self, data):
        self.assertEqual(codec.decompress(codec.compress(data)), data)

    def test_edge_cases(self):
        for data in (b'', b'a', b'ab', b'aaa', b'\xff' * 5000, bytes(range(256)) * 20):
            self.roundtrip(data)

    def test_random_and_repetitive(self):
        rnd = random.Random(7)
        for n in (10, 999, 40000):
            self.roundtrip(bytes(rnd.choice(b'\x00\x01\x02abc') for _ in range(n)) + os.urandom(n // 20))

    def test_long_distances(self):
        block = os.urandom(0x1400)                  # repeats beyond 0xD00 exercise the length bonus
        self.roundtrip(block + os.urandom(100) + block + block[:3] + block[5:40])

    def test_blob_record(self):
        rec = codec.compress_checked(b'hello hello hello')
        size, checksum = int.from_bytes(rec[:4], 'big'), int.from_bytes(rec[4:8], 'big')
        self.assertEqual(size, len(rec) - 8)
        self.assertEqual(checksum, sum(rec[8:]) & 0xFFFFFFFF)
        self.assertEqual(codec.decompress_blob(rec, 0), b'hello hello hello')
        bad = bytearray(rec); bad[-1] ^= 1
        with self.assertRaises(codec.CodecError):
            codec.decompress_blob(bytes(bad), 0)

    def test_hand_made_stream(self):
        # literal 'A' (bit 1 + raw), then the end marker: bit 0, vlq(0x1000002), flush, 0xFF
        bits = [1] + [0]
        n = 0x1000002; k = n ^ (1 << (n.bit_length() - 1))
        for i in range(n.bit_length() - 2, -1, -1):
            bits += [(k >> i) & 1, 0 if i else 1]
        first, rest = bits[:8], bits[8:]
        out = bytearray([int(''.join(map(str, first)), 2), ord('A')])
        while rest:
            chunk, rest = rest[:8], rest[8:]
            out.append(int(''.join(map(str, chunk + [0] * (8 - len(chunk)))), 2))
        out.append(0xFF)
        self.assertEqual(codec.decompress(bytes(out)), b'A')

    def test_truncated(self):
        with self.assertRaises(codec.CodecError):
            codec.decompress(codec.compress(b'x' * 100)[:-3])


if __name__ == '__main__':
    unittest.main()
