"""Checks against a real OS 1.63 image. Skipped unless MDKIT_FIRMWARE points at
elektron_sps1-1uw_os1.63.bin (and optionally MDKIT_SYSEX at the stock .syx)."""
import os
import unittest
from pathlib import Path

import helpers  # noqa: F401
from mdkit import codec, image as img, machine as mc, sysex

FW = os.environ.get('MDKIT_FIRMWARE')


@unittest.skipUnless(FW, 'set MDKIT_FIRMWARE to the stock image')
class Stock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = Path(FW).read_bytes()
        cls.payloads, cls.report = img.extract(cls.image)

    def test_identity_and_sizes(self):
        self.assertTrue(self.report['stock'])
        self.assertEqual(self.report['version'], '163 ')
        sizes = {k: len(v) for k, v in self.payloads.items()}
        self.assertEqual(sizes, {'MainOS': 404766, 'DSP2': 750369, 'DSP1': 56469,
                                 'OS_A': 524288, 'OS_B': 524288})

    def test_dsp_streams(self):
        for name, sections in (('DSP1', 58), ('DSP2', 135)):
            s = img.DspStream(self.payloads[name])
            self.assertEqual((s.entry, len(s.sections)), (0x24, sections))

    def test_structures(self):
        main = self.payloads['MainOS']
        fams = mc.read_families(main)
        self.assertEqual([f[1] for f in fams],
                         ['GND', 'TRX', 'EFM', 'E12', 'P-I', 'INP', 'MID', 'CTR', 'ROM', 'RAM'])
        self.assertEqual(mc.read_list(main, mc.GND_LIST), [0x24EF54, 0x24EFAA, 0x24F000, 0x24F056])
        s = img.DspStream(self.payloads['DSP2'])
        for i in mc.FREE_IDS:
            self.assertEqual(img.mainos_read(main, mc.ID_TABLE + 4 * i), (0x24EF54).to_bytes(4, 'big'))
            self.assertTrue(mc.dsp_type_free(s, i + 1), i)
        # ID 29 points at the empty descriptor but its DSP type runs stock code.
        self.assertEqual(img.mainos_read(main, mc.ID_TABLE + 4 * 29), (0x24EF54).to_bytes(4, 'big'))
        self.assertFalse(mc.dsp_type_free(s, 30))
        self.assertEqual([s.read(t) for t in mc.DISPATCH_TABLES], [0x10008E, 0x10008E, 0x10008F])
        self.assertEqual([s.read(t + 2) for t in mc.DISPATCH_TABLES], [0x1000A3, 0x1000A4, 0x1000AD])
        self.assertFalse(s.overlaps(0x1F0000, 0x10000))

    def test_budget_sites(self):
        main = self.payloads['MainOS']
        self.assertEqual(img.mainos_read(main, 0x200376, 4), bytes.fromhex('70144840'))
        for branch, (rom_site, form, rom, total_site, total, _) in mc.BUDGET_SITES.items():
            if form == 'long':
                self.assertEqual(img.mainos_read(main, rom_site), rom.to_bytes(4, 'big'))
            self.assertEqual(img.mainos_read(main, total_site), total.to_bytes(4, 'big'))
        self.assertEqual(img.mainos_read(main, mc.GUARD_A_SITE, 6), mc.GUARD_A_OLD)
        self.assertEqual(img.mainos_read(main, mc.GUARD_B_SITE, 8), mc.GUARD_B_OLD)

    def test_sysex_matches_vendor_file(self):
        payload = sysex.image_payload(self.image)
        self.assertEqual((len(payload), 0x4000 + len(payload)), (939744, 0xE96E0))
        syx = os.environ.get('MDKIT_SYSEX')
        if syx:
            self.assertEqual(sysex.encode_payload(payload), Path(syx).read_bytes())

    def test_mainos_recompresses(self):
        rec = codec.compress_checked(self.payloads['MainOS'])
        self.assertLess(len(rec), 0x28991 + 0x1000)


if __name__ == '__main__':
    unittest.main()
