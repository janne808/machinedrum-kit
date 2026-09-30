import unittest

import helpers
from mdkit import image as img


class Image(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.payloads = helpers.fake_image()

    def test_extract(self):
        payloads, report = img.extract(self.image)
        self.assertEqual(payloads, self.payloads)
        self.assertEqual(report['version'], '163 ')
        dsp2 = next(b for b in report['blobs'] if b['name'] == 'DSP2')['dsp']
        self.assertEqual(dsp2['entry'], 0x24)
        self.assertEqual(len(dsp2['sections']), 3)

    def test_checksum_failure(self):
        bad = bytearray(self.image); bad[0x4010] ^= 0xFF
        with self.assertRaises(ValueError):
            img.extract(bytes(bad))

    def test_dsp_stream_edits(self):
        s = img.DspStream(self.payloads['DSP2'])
        self.assertEqual(s.read(0x145AF5 + 16), 0x10008E)
        s.write(0x145AF5 + 16, 0x1FA000, expected=s.read(0x145AF5))
        with self.assertRaises(img.ImageError):
            s.write(0x145AF5 + 16, 0x1, expected=0x10008E)     # no longer the fallback
        with self.assertRaises(img.ImageError):
            s.read(0x999999)                                     # not uploaded
        s.append_section('P', 0x1FA000, [1, 2, 3])
        self.assertEqual(s.read(0x1FA002), 3)
        self.assertEqual((s.entry, s.config), (0x24, 0x123456))
        self.assertTrue(s.overlaps(0x1FA000, 0x1000))
        self.assertFalse(img.DspStream(self.payloads['DSP2']).overlaps(0x1FA000, 0x1000))

    def test_pack_and_verify(self):
        main = bytearray(self.payloads['MainOS']); main[100:104] = b'EDIT'
        bank = bytes([0x10, 0x0F, 0xF0, 0x00]) + b'\xff' * 0xFFC        # an alias pointer: fine
        image, records = img.pack_image(self.image, {'MainOS': bytes(main)}, {0xFF000: bank}, version='163T')
        self.assertEqual([r['name'] for r in records], list(img.ORDER))
        payloads, report, problems = img.verify_image(image, self.image, {'MainOS': main})
        self.assertEqual(problems, [])
        self.assertEqual(report['version'], '163T')
        self.assertEqual(image[0xFF000:0x100000], bank)
        self.assertEqual(image[0x200000:0x200010], b'upper flash data')
        self.assertEqual(img.low_window_pointers(image, {0xFF000: bank}), [])

    def test_low_window_detection(self):
        bank = bytes([0x00, 0x0F, 0xF1, 0x00]) + b'\xff' * 0xFFC        # 0xFF100: hangs hardware
        hits = img.low_window_pointers(None, {0xFF000: bank})
        self.assertEqual(hits, [('bank', 0xFF000, 0xFF100)])
        self.assertEqual(img.low_window_pointers(None, {0xFF000: b'\xff' * 8}, [(0x252092, 0xFF000)]),
                         [('mainos', 0x252092, 0xFF000)])

    def test_pack_refuses_bad_layouts(self):
        with self.assertRaises(img.ImageError):
            img.pack_image(self.image, {}, {0xFF000: b'x' * 0x2000})          # past 0x100000
        with self.assertRaises(img.ImageError):
            img.pack_image(self.image, {}, {0x4100: b'x'})                    # inside the chain

    def test_versions(self):
        for good in ('163N', '2XX ', '999Z'):
            img.check_version(good)
        for bad in ('163', ' 163', '139Z', '163n', '14\x1f0'):
            with self.assertRaises(img.ImageError):
                img.check_version(bad)

    def test_flash_cpu(self):
        self.assertEqual(img.flash_cpu(0xFF200), 0x100FF200)


if __name__ == '__main__':
    unittest.main()
