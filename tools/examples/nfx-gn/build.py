#!/usr/bin/env python3
"""Build a stock OS 1.63 image with NFX-GN, the kit's worked example
(docs/11-writing-a-custom-machine.md), in a new NFX family. All stock machines,
including RAM recording and playback, stay available.

The build:
  - reserves DSP2 memory from 0x1F0000 up for custom code (sample budgets +
    loader guards; RAM machines keep working with slightly less memory);
  - relocates the family table to flash and appends an eleventh family, NFX;
  - adds NFX-GN as firmware ID 15 / DSP type 16, code at P:0x1FA000;
  - writes machinedrum-nfx-gn.bin, .syx and build.json.

  python3 build.py --firmware elektron_sps1-1uw_os1.63.bin \
      --asm /path/to/dsp56300-asm --output out/
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from mdkit import image as img, machine as mc, sysex, toolchain  # noqa: E402

ID = 15
DSP_TYPE = ID + 1
CODE_REGION = mc.CODE_REGION             # 0x1F0000: reserved from here up
DSP_BANK, DSP_CAPACITY = 0x1FA000, 0x1000
FLASH_BANK = 0xFF000
TABLE_OFF, MENU_OFF, DESC_OFF, CODE_OFF = 0x000, 0x060, 0x080, 0x200


def build(firmware, asm, output, prefix='m68k-linux-gnu-', version=None):
    original = Path(firmware).read_bytes()
    if img.sha256(original) != img.STOCK_SHA256:
        raise SystemExit('this example patches the stock OS 1.63 image only')
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    work = out / 'work'

    code, entries, _ = toolchain.assemble_dsp(asm, HERE / 'machine.s', DSP_BANK, DSP_CAPACITY, work / 'dsp')
    cf_code, cf = toolchain.link_coldfire(
        [HERE / 'control.s', HERE.parent / 'common' / 'guards.s'],
        img.flash_cpu(FLASH_BANK + CODE_OFF), 'gain_control', work / 'coldfire', prefix,
        defsyms={'GUARD_CEILING': CODE_REGION})
    if CODE_OFF + len(cf_code) > 0x1000:
        raise SystemExit('ColdFire code does not fit the flash bank')

    payloads, _ = img.extract(original)
    main = bytearray(payloads['MainOS'])
    dsp = img.DspStream(payloads['DSP2'])

    table_cpu = img.flash_cpu(FLASH_BANK + TABLE_OFF)
    menu_cpu = img.flash_cpu(FLASH_BANK + MENU_OFF)
    desc_cpu = img.flash_cpu(FLASH_BANK + DESC_OFF)
    desc = mc.descriptor(main, cf['gain_control'], ID, 'NFX', 'GN',
                         ['GAIN', '', '', '', '', '', '', ''], [64, 0, 0, 0, 0, 0, 0, 0])

    # MainOS: code region, family table with NFX appended, ID 15.
    plan = mc.reserve_sample_memory(main, CODE_REGION, cf['guard_loader_a'], cf['guard_loader_b'])
    table = mc.relocate_family_table(main, table_cpu, [('NFX', menu_cpu)])
    mc.register_id(main, ID, desc_cpu, dsp)

    mc.set_dispatch(dsp, DSP_TYPE, entries)
    mc.add_program(dsp, DSP_BANK, DSP_CAPACITY, code)

    bank = bytearray(b'\xff' * 0x1000)
    bank[TABLE_OFF:TABLE_OFF + len(table)] = table
    menu = mc.menu_list([desc_cpu])
    bank[MENU_OFF:MENU_OFF + len(menu)] = menu
    bank[DESC_OFF:DESC_OFF + len(desc)] = desc
    bank[CODE_OFF:CODE_OFF + len(cf_code)] = cf_code
    if TABLE_OFF + len(table) > MENU_OFF:
        raise SystemExit('family table overlaps the menu')
    banks = {FLASH_BANK: bytes(bank)}

    edited = {'MainOS': bytes(main), 'DSP2': dsp.bytes()}
    image, records = img.pack_image(original, edited, banks, version=version)
    stock_main = payloads['MainOS']
    edits = [(img.MAIN_BASE + o, int.from_bytes(main[o:o + 4], 'big'))
             for o in range(0, len(main) - 3, 2) if main[o:o + 4] != stock_main[o:o + 4]]
    if img.low_window_pointers(image, banks, edits):
        raise SystemExit('low-window flash pointers')
    _, report, problems = img.verify_image(image, original, edited)
    if problems:
        raise SystemExit('; '.join(problems))
    syx = sysex.encode_payload(sysex.image_payload(image))
    if sysex.apply_payload(sysex.decode_sysex(syx)[0], original) != image:
        raise SystemExit('SysEx does not reconstruct the image')

    (out / 'machinedrum-nfx-gn.bin').write_bytes(image)
    (out / 'machinedrum-nfx-gn.syx').write_bytes(syx)
    manifest = {'sha256': img.sha256(image), 'version': report['version'], 'blobs': records,
                'reservation': {'from': CODE_REGION, 'budgets': plan},
                'machine': {'id': ID, 'dsp_type': DSP_TYPE, 'bank': DSP_BANK, 'words': len(code),
                            'entries': dict(zip(mc.ENTRY_SYMBOLS, entries)),
                            'family_table_cpu': table_cpu, 'family_index': 10,
                            'descriptor_cpu': desc_cpu, 'menu_cpu': menu_cpu,
                            'handler_cpu': cf['gain_control']}}
    (out / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--firmware', required=True, type=Path)
    ap.add_argument('--asm', required=True, type=Path, help='dsp56300-asm binary')
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--binutils-prefix', default='m68k-linux-gnu-')
    ap.add_argument('--version', help='4-character version record, e.g. 163G')
    a = ap.parse_args()
    m = build(a.firmware, a.asm, a.output, a.binutils_prefix, a.version)
    print(json.dumps({'sha256': m['sha256'], 'machine': m['machine']}, indent=2))
