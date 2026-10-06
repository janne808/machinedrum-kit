#!/usr/bin/env python3
"""Build a stock OS 1.63 image with GND-SW, a MIDI-tuned PolyBLEP saw, as a
fifth GND machine, in the E12 layout: the 16 E12 machines make room for custom
code, and sample memory, the ROM budget and RAM recording and playback stay
stock.

The build:
  - removes the E12 machines (IDs, DSP dispatch, family record) and their 42
    sample sections from the DSP2 upload;
  - uploads the DSP code at P:0x127000, in E12's former sample area (bus
    area 0), and points DSP type 9 at it;
  - registers firmware ID 8 and appends GND-SW to the GND menu;
  - writes machinedrum-gnd-sw.bin, .syx and build.json.

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

ID = 8
DSP_TYPE = ID + 1
DSP_BANK, DSP_CAPACITY = 0x127000, 0x1000   # inside mc.E12_CODE_REGION
FLASH_BANK = 0xFF000                      # file offset of the 4 KiB flash bank
DESC_OFF, MENU_OFF, CODE_OFF = 0x000, 0x100, 0x200


def build(firmware, asm, output, prefix='m68k-linux-gnu-', version=None):
    original = Path(firmware).read_bytes()
    if img.sha256(original) != img.STOCK_SHA256:
        raise SystemExit('this example patches the stock OS 1.63 image only')
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    work = out / 'work'

    code, entries, _ = toolchain.assemble_dsp(asm, HERE / 'machine.s', DSP_BANK, DSP_CAPACITY, work / 'dsp')
    cf_code, cf = toolchain.link_coldfire(
        [HERE / 'control.s'],
        img.flash_cpu(FLASH_BANK + CODE_OFF), 'saw_control', work / 'coldfire', prefix,
        include_dirs=[HERE])
    if CODE_OFF + len(cf_code) > 0x1000:
        raise SystemExit('ColdFire code does not fit the flash bank')

    payloads, _ = img.extract(original)
    main = bytearray(payloads['MainOS'])
    dsp = img.DspStream(payloads['DSP2'])

    # Descriptor and GND menu: the four stock GND entries, then GND-SW.
    desc_cpu = img.flash_cpu(FLASH_BANK + DESC_OFF)
    menu_cpu = img.flash_cpu(FLASH_BANK + MENU_OFF)
    desc = mc.descriptor(main, cf['saw_control'], ID, 'GND', 'SW',
                         ['PTCH', 'DEC', 'RAMP', 'RDEC', '', '', '', ''], [73, 96, 0, 0, 0, 0, 0, 0])
    families = mc.read_families(main)
    _, name, gnd_list = families[0]
    assert name == 'GND' and gnd_list == mc.GND_LIST, families
    menu = mc.menu_list(mc.read_list(main, gnd_list) + [desc_cpu])

    # E12 out (MainOS and DSP2), then register the ID and point GND at the new menu.
    removed = mc.remove_e12_machines(main, dsp)
    mc.register_id(main, ID, desc_cpu, dsp)
    mc.set_family_list(main, 0, gnd_list, menu_cpu)

    # DSP2: dispatch cells for type 9 and the program upload.
    mc.set_dispatch(dsp, DSP_TYPE, entries)
    if not mc.E12_CODE_REGION[0] <= DSP_BANK < sum(mc.E12_CODE_REGION):
        raise SystemExit('bank outside the E12 code region')
    mc.add_program(dsp, DSP_BANK, DSP_CAPACITY, code, reserved=[mc.E12_POOL])

    bank = bytearray(b'\xff' * 0x1000)
    bank[DESC_OFF:DESC_OFF + len(desc)] = desc
    bank[MENU_OFF:MENU_OFF + len(menu)] = menu
    bank[CODE_OFF:CODE_OFF + len(cf_code)] = cf_code
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

    (out / 'machinedrum-gnd-sw.bin').write_bytes(image)
    (out / 'machinedrum-gnd-sw.syx').write_bytes(syx)
    manifest = {'sha256': img.sha256(image), 'version': report['version'], 'blobs': records,
                'layout': {'e12_words_removed': removed, 'code_region': mc.E12_CODE_REGION,
                           'pool': mc.E12_POOL, 'sample_memory': 'stock'},
                'machine': {'id': ID, 'dsp_type': DSP_TYPE, 'bank': DSP_BANK, 'words': len(code),
                            'entries': dict(zip(mc.ENTRY_SYMBOLS, entries)),
                            'descriptor_cpu': desc_cpu, 'menu_cpu': menu_cpu,
                            'handler_cpu': cf['saw_control']}}
    (out / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--firmware', required=True, type=Path)
    ap.add_argument('--asm', required=True, type=Path, help='dsp56300-asm binary')
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--binutils-prefix', default='m68k-linux-gnu-')
    ap.add_argument('--version', help='4-character version record, e.g. 163S')
    a = ap.parse_args()
    m = build(a.firmware, a.asm, a.output, a.binutils_prefix, a.version)
    print(json.dumps({'sha256': m['sha256'], 'machine': m['machine']}, indent=2))
