import unittest

import helpers  # noqa: F401
from mdkit import image as img, machine as mc


def fake_mainos():
    """Just enough MainOS for the registration helpers: template descriptor,
    ID table, GND list, family table and the eight family references."""
    m = bytearray(0x62D1E)
    def put(addr, data):
        m[addr - img.MAIN_BASE:addr - img.MAIN_BASE + len(data)] = data
    tpl = bytearray(86); tpl[0:4] = (0x201130).to_bytes(4, 'big'); tpl[4] = 1; tpl[5:10] = b'GNDSN'
    put(mc.GND_SIN_DESCRIPTOR, bytes(tpl))
    for i in range(192):
        put(mc.ID_TABLE + 4 * i, (0x24EF54 if i in mc.FREE_IDS or i == 29 else 0x24F000 + i).to_bytes(4, 'big'))
    names = [b'GND', b'TRX', b'EFM', b'E12', b'P-I', b'INP', b'MID', b'CTR', b'ROM', b'RAM']
    for i, n in enumerate(names):
        put(mc.FAMILY_TABLE + 8 * i, n.ljust(4, b'\0') + (0x251E3E + 0x20 * i).to_bytes(4, 'big'))
    for ref in mc.FAMILY_TABLE_REFS:
        put(ref, mc.FAMILY_TABLE.to_bytes(4, 'big'))
    for ref in mc.FAMILY_LIST_REFS:
        put(ref, (mc.FAMILY_TABLE + 4).to_bytes(4, 'big'))
    put(0x200376, bytes.fromhex('70144840'))
    for addr, value in ((0x200382, 0x15F400), (0x2003AA, 0x0F05F0), (0x2003B6, 0x0FFFF0)):
        put(addr, value.to_bytes(4, 'big'))
    put(mc.GUARD_A_SITE, mc.GUARD_A_OLD)
    put(mc.GUARD_B_SITE, mc.GUARD_B_OLD)
    return m


class Machine(unittest.TestCase):
    def test_descriptor(self):
        m = fake_mainos()
        d = mc.descriptor(m, 0x100FF200, 15, 'NFX', 'GN', ['GAIN'] + [''] * 7, [64] + [0] * 7)
        self.assertEqual(len(d), 86)
        self.assertEqual(d[0:4], (0x100FF200).to_bytes(4, 'big'))
        self.assertEqual(d[4:10], b'\x0fNFXGN')
        self.assertEqual(d[10:14], b'GAIN')
        self.assertEqual(d[42], 64)
        self.assertEqual(d[50:54], b'\x11' * 4)
        with self.assertRaises(img.ImageError):
            mc.descriptor(m, 0, 15, 'NFX', 'gn', [''] * 8, [0] * 8)

    def test_register_and_families(self):
        m = fake_mainos()
        mc.register_id(m, 15, 0x100FF000)
        with self.assertRaises(img.ImageError):
            mc.register_id(m, 15, 0x100FF000)                 # already taken
        with self.assertRaises(img.ImageError):
            mc.register_id(m, 20, 0x100FF000)                 # not a free ID
        for ident in (29, 95, 114):                            # empty descriptor, but not free
            with self.assertRaises(img.ImageError):
                mc.register_id(m, ident, 0x100FF000)
        class Dsp:                                             # dispatch cells: fallback except type 41
            def read(self, a):
                return 0x1F0000 if a - 41 in mc.DISPATCH_TABLES else a & 0xFF000000 or 0x10008E
        with self.assertRaises(img.ImageError):
            mc.register_id(m, 40, 0x100FF080, Dsp())          # DSP type 41 in use
        mc.register_id(m, 41, 0x100FF080, Dsp())               # extra free ID, DSP type 42 unused
        self.assertEqual(img.mainos_read(m, mc.ID_TABLE + 4 * 41), (0x100FF080).to_bytes(4, 'big'))
        fams = mc.read_families(m)
        self.assertEqual(fams[9][1], 'RAM')
        mc.rename_family(m, 9, 'RAM', 'NFX')
        mc.set_family_list(m, 9, fams[9][2], 0x100FF100)
        self.assertEqual(mc.read_families(m)[9][1:], ('NFX', 0x100FF100))

    def test_relocate_family_table(self):
        m = fake_mainos()
        table = mc.relocate_family_table(m, 0x100FE000, [('NFX', 0x100FE060)])
        self.assertEqual(len(table), 8 * 12)
        self.assertEqual(table[80:88], b'NFX\0' + (0x100FE060).to_bytes(4, 'big'))
        self.assertEqual(table[88:], bytes(8))
        for ref in mc.FAMILY_TABLE_REFS:
            self.assertEqual(img.mainos_read(m, ref), (0x100FE000).to_bytes(4, 'big'))
        for ref in mc.FAMILY_LIST_REFS:
            self.assertEqual(img.mainos_read(m, ref), (0x100FE004).to_bytes(4, 'big'))

    def test_dispatch_and_program(self):
        _, payloads = helpers.fake_image()
        s = img.DspStream(payloads['DSP2'])
        mc.set_dispatch(s, 16, [0x1FA000, 0x1FA001, 0x1FA002])
        self.assertEqual([s.read(t + 16) for t in mc.DISPATCH_TABLES], [0x1FA000, 0x1FA001, 0x1FA002])
        mc.clear_dispatch(s, 16)
        self.assertEqual(s.read(mc.DISPATCH_TABLES[2] + 16), 0x10008F)
        mc.add_program(s, 0x1FA000, 0x1000, [0xC] * 10)
        with self.assertRaises(img.ImageError):
            mc.add_program(s, 0x1FA000, 0x1000, [0xC])        # bank now occupied
        with self.assertRaises(img.ImageError):
            mc.add_program(s, 0x1FB000, 4, [0] * 5)            # too big

    def test_sample_reservation(self):
        self.assertEqual(mc.CODE_REGION, mc.DELAY_POOL[0] + mc.DELAY_POOL[1])   # pool directly below the code
        code = mc.sample_reservation(mc.CODE_REGION)        # 0x1B0000, bus area 2
        self.assertEqual((code['48-ROM']['rom'], code['48-ROM']['total']), (0x0A0000, 0x0C0000))
        self.assertEqual((code['32-ROM']['rom'], code['32-ROM']['total']), (0x050600, 0x060000))
        pool = mc.sample_reservation(mc.DELAY_POOL[0])      # delay pool + code region, 0x190000
        self.assertEqual((pool['48-ROM']['rom'], pool['48-ROM']['total']), (0x060000, 0x080000))
        self.assertEqual((pool['32-ROM']['rom'], pool['32-ROM']['total']), (0x010600, 0x020000))
        old = mc.sample_reservation(0x1F0000)               # the earlier area-3 code region
        self.assertEqual((old['48-ROM']['rom'], old['48-ROM']['total']), (0x120000, 0x140000))
        self.assertEqual((old['32-ROM']['rom'], old['32-ROM']['total']), (0x0D0600, 0x0E0000))
        for plan in (code, pool, old):                           # stock RAM minimum kept (or more)
            self.assertGreaterEqual(plan['48-ROM']['total'] - plan['48-ROM']['rom'], 0x15F400 - 0x140000)
            self.assertEqual(plan['32-ROM']['total'] - plan['32-ROM']['rom'], 0x0FFFF0 - 0x0F05F0)
        with self.assertRaises(img.ImageError):
            mc.sample_reservation(0x140000)                 # below the 48-ROM sample base

    def test_reserve_sample_memory_keeps_ram_machines(self):
        m = fake_mainos()
        ids_before = img.mainos_read(m, mc.ID_TABLE, 4 * 192)
        mc.reserve_sample_memory(m, mc.CODE_REGION, 0x100FF222, 0x100FF256)
        self.assertEqual(img.mainos_read(m, 0x200376, 2), bytes([0x70, 0x0A]))
        self.assertEqual(img.mainos_read(m, 0x200382), (0x0C0000).to_bytes(4, 'big'))
        self.assertEqual(img.mainos_read(m, 0x2003B6), (0x060000).to_bytes(4, 'big'))
        self.assertEqual(img.mainos_read(m, mc.GUARD_A_SITE, 6), bytes.fromhex('4eb9100ff222'))
        self.assertEqual(img.mainos_read(m, mc.GUARD_B_SITE, 8), bytes.fromhex('4eb9100ff2564e71'))
        self.assertEqual(img.mainos_read(m, mc.ID_TABLE, 4 * 192), ids_before)   # RAM machines untouched
        with self.assertRaises(img.ImageError):
            mc.reserve_sample_memory(m, mc.CODE_REGION, 0, 0)  # already applied

    def test_lod(self):
        lod = '\n'.join(['P 1FA000 00000C', 'P 1FA001 00000C', 'P 1FA002 00000C',
                         'I 1FA000 machine_init', 'I 1FA001 machine_update', 'I 1FA002 machine_render'])
        words, entries, _ = mc.program_from_lod(lod, 0x1FA000, 0x1000)
        self.assertEqual(words, [0xC] * 3)
        self.assertEqual(entries, [0x1FA000, 0x1FA001, 0x1FA002])
        with self.assertRaises(img.ImageError):
            mc.program_from_lod(lod.replace('P 1FA001', 'P 1FA009'), 0x1FA000, 0x1000)


if __name__ == '__main__':
    unittest.main()
