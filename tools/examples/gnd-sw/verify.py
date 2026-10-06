#!/usr/bin/env python3
"""Verify a GND-SW build (build.py output) in the kit's emulator.

1. Kernel: md-kernel runs the DSP code outside the firmware in both DSP engines,
   with poisoned registers and memory; every output sample, the state words and
   the oscillator scratch must equal model.py.
2. Booted image: the E12 layout (E12 gone from IDs, dispatch and families;
   stock sample budgets and RAM slots), RAM-R1 still recording, registration
   read back, front-panel selection (GND-SW, and RAM in its shifted family
   slot), and live audio/state equal to the model at six knob settings.

  python3 verify.py --build out/ --firmware elektron_sps1-1uw_os1.63.bin [--kernel-only]
"""
import argparse
import json
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'emulator')]

from mdkit import image as img  # noqa: E402
from model import Model, Q, note_step  # noqa: E402

KERNEL = TOOLS / 'emulator' / 'build' / 'md-kernel'


def signed(w):
    return (w & 0x7FFFFF) - (w & 0x800000)


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)
    if not ok:
        raise SystemExit(1)


def kernel_tests(build, decay):
    m = build['machine']
    code = (Path(build['_dir']) / 'work' / 'dsp' / 'machine.lod').read_text()
    from mdkit.machine import program_from_lod
    words, entries, _ = program_from_lod(code, m['bank'], 0x1000)
    cases = {
        'all MIDI notes': [(b == 0, [note_step(n), decay(127), 0, decay(0)]) for n in range(0, 128, 3) for b in range(24)],
        'ramp, decay, retrigger': [(b % 67 == 0, [note_step([0, 69, 127][(b // 43) % 3]), decay([0, 64, 127][(b // 51) % 3]),
                                                  [0, 0x80000, 0x1F8080][(b // 39) % 3], decay([0, 64, 127][(b // 61) % 3])])
                                   for b in range(1024)],
        'Nyquist clamp': [(b == 0, [note_step(127), Q - 1, 0x1F8080, Q - 1]) for b in range(256)],
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'program.bin').write_bytes(img.words_le(words))
        for name, blocks in cases.items():
            model, rec, expected = Model(), [], []
            for trig, packet in blocks:
                out, raw = model.process(packet, trig)
                rec += [2 if trig else 0] + packet + [0] * 32
                expected.append((out, model.s, raw))
            (tmp / 'in.bin').write_bytes(struct.pack(f'<{len(rec)}I', *rec))
            for engine in ('jit', 'interpreter'):
                r = subprocess.run([str(KERNEL), '--program', str(tmp / 'program.bin'), '--bank', hex(m['bank']),
                                    '--init', str(entries[0]), '--update', str(entries[1]), '--render', str(entries[2]),
                                    '--input', str(tmp / 'in.bin'), '--output', str(tmp / 'out.bin'),
                                    '--packet', '4', '--engine', engine], capture_output=True, text=True)
                if r.returncode:
                    raise SystemExit(r.stderr)
                data = (tmp / 'out.bin').read_bytes()
                got = struct.unpack(f'<{len(data) // 4}I', data)
                for i, (out, state, raw) in enumerate(expected):
                    base = i * 128
                    ok = (list(got[base:base + 32]) == [v & 0xFFFFFF for v in out]
                          and list(got[base + 32 + 6:base + 32 + 11]) == [v & 0xFFFFFF for v in state]
                          and list(got[base + 96:base + 128]) == [v & 0xFFFFFF for v in raw])
                    if not ok:
                        raise SystemExit(f'FAIL kernel {name} {engine}: block {i} differs from the model')
                cycles = re.search(r'worst_block_cycles=(\d+)', r.stdout)[1]
                check(f'kernel {name} ({engine}): {len(blocks)} blocks exact, guards pass' + (f', worst {cycles} cycles' if engine == 'jit' else ''), True)


def booted(build, stock_main):
    from monitor import MonitorClient
    m = build['machine']
    e = m['entries']
    image = Path(build['_dir']) / 'machinedrum-gnd-sw.bin'

    def smain(a):
        return int.from_bytes(stock_main[a - 0x200000:a - 0x200000 + 4], 'big')

    with MonitorClient(image, experimental_firmware=True, load_symbols=False) as mon:
        def cmd(c):
            r = mon.execute(c)
            while isinstance(r, dict) and r.get('paused') is False:
                r = mon.execute('wait 60000')
            return r

        def go(n=4410):
            assert cmd(f'continue {n}')['reason'] == 'frame_budget'

        def mem(cpu, space, address, count=1):
            cmd('cpu ' + cpu)
            return cmd(f'memory {space} {address} {count}')['values']

        def u32(a):
            return int.from_bytes(bytes(mem('coldfire', 'B', a, 4)), 'big')

        def assign(t, ident, uw=0):
            cmd(f'midi 0xf0 0 0x20 0x3c 2 0 0x5b {t} {ident} {uw} 2 0xf7'); go(22050)

        def press(row, mask):
            cmd(f'panel {row} {mask}'); go(2205); cmd(f'panel {row} 0'); go(4410)

        def stop(pc):
            cmd('cpu dsp2'); bp = cmd(f'break {pc}')['id']; r = cmd('continue 44100'); cmd(f'delete {bp}')
            assert r.get('reason') == 'breakpoint' and r['hit_id'] == bp, r

        check('boot', cmd('boot')['reason'] == 'boot_ready'); go(330750)
        rom, total, base = u32(0x29E9F0), u32(0x29F6DE), u32(0x29F38E)
        check(f'48-ROM budgets stock: ROM 0x{rom:x} total 0x{total:x}', (rom, total, base) == (0x140000, 0x15F400, 0x150000))
        slots = mem('dsp2', 'Y', 0x147E80, 16)[::4]
        end_rom = u32(0x26541E)
        stride = (total // 2 - end_rom) // 4
        check(f'RAM slots stock, 0x{slots[0]:x}..0x{slots[3] + stride:x}',
              slots == [base + end_rom + i * stride for i in range(4)] and slots[3] + stride <= 0x1FFA00)
        names = [bytes(mem('coldfire', 'B', 0x252396 + 8 * i, 4)).rstrip(b'\0').decode() for i in range(10)]
        check(f'families {names[:9]}', names == ['GND', 'TRX', 'EFM', 'P-I', 'INP', 'MID', 'CTR', 'ROM', 'RAM', '']
              and u32(0x252396 + 76) == 0)
        fallback = [mem('dsp2', 'Y', t)[0] for t in (0x145AF5, 0x145BB6, 0x145C77)]
        check('E12 IDs 48-63 empty, DSP types 49-64 on the fallback',
              all(u32(0x252092 + 4 * i) == 0x24EF54 for i in range(48, 64))
              and all(mem('dsp2', 'Y', t + i + 1)[0] == f for i in range(48, 64) for t, f in zip((0x145AF5, 0x145BB6, 0x145C77), fallback)))
        check('RAM family and RAM IDs unchanged',
              bytes(mem('coldfire', 'B', 0x252396 + 64, 4)) == b'RAM\0' and u32(0x252396 + 68) == smain(0x252396 + 76)
              and all(u32(0x252092 + 4 * i) == smain(0x252092 + 4 * i) for i in (160, 161, 162, 163, 165, 166, 167, 168)))
        assign(0, 32, 1)
        cmd('cpu dsp2'); bp = cmd('break 0x103579')['id']; cmd('midi 0x90 36 120'); r = cmd('continue 44100'); cmd(f'delete {bp}')
        check('RAM-R1 renders from its staging area', r.get('hit_id') == bp and mem('dsp2', 'Y', 0x819)[0] == 0x135206)
        stop(0x10369C); go(4410)
        check('RAM-R1 records into its slot', mem('dsp2', 'Y', 0x819)[0] == slots[0] and mem('dsp2', 'Y', 0x147E81)[0] > 0x60)
        check('ID 8 and GND menu', u32(0x252092 + 32) == m['descriptor_cpu'] and u32(0x25239A) == m['menu_cpu']
              and [u32(m['menu_cpu'] + 4 * i) for i in range(6)] == [0x24EF54, 0x24EFAA, 0x24F000, 0x24F056, m['descriptor_cpu'], 0])
        check('dispatch type 9', [mem('dsp2', 'Y', t + 9)[0] for t in (0x145AF5, 0x145BB6, 0x145C77)]
              == [e['machine_init'], e['machine_update'], e['machine_render']])
        assign(0, 1)
        press(0x22, 0x40); press(0x23, 0x20); press(0x24, 0x10); press(0x23, 0x40); press(0x24, 0x08)
        for _ in range(10):
            press(0x24, 0x10)
        check('browser on GND with 5 entries', u32(0x28B72C) == 0 and u32(0x28C2D8) == 5)
        press(0x23, 0x80)
        for _ in range(5):
            press(0x24, 0x10)
        for _ in range(4):
            press(0x23, 0x40)
        press(0x24, 0x08)
        check('front panel assigns ID 8', u32(0x7001AA) == 8)
        if u32(0x281A46) == 23:
            press(0x23, 0x10)
        for _ in range(3):
            press(0x23, 0x10)                 # back to the main screen
        assign(0, 1)
        press(0x22, 0x40); press(0x23, 0x20); press(0x24, 0x10); press(0x23, 0x40); press(0x24, 0x08)
        for _ in range(8):
            press(0x23, 0x40)
        check(f'browser on RAM (family {u32(0x28B72C)}) with {u32(0x28C2D8)} entries', u32(0x28B72C) == 8 and u32(0x28C2D8) == 8)
        press(0x23, 0x80); press(0x24, 0x08)
        check(f'front panel assigns a RAM machine (ID {u32(0x7001AA)})', u32(0x7001AA) in (160, 161, 162, 163, 165, 166, 167, 168))
        for _ in range(3):
            press(0x23, 0x10)
        assign(0, 8)
        for note, dec, ramp, rdec in [(0, 96, 0, 0), (60, 96, 0, 0), (69, 127, 0, 0), (127, 96, 0, 0), (69, 0, 0, 127), (69, 96, 127, 127)]:
            for cc, v in enumerate((note, dec, ramp, rdec), 16):
                cmd(f'midi 0xb0 {cc} {v}')
            go(22050); cmd('midi 0x90 36 120'); stop(e['machine_update']); stop(e['machine_render'])
            model = Model(); model.s = mem('dsp2', 'Y', 0x806, 5)
            for block in range(64):
                packet = mem('dsp2', 'Y', 0x801, 4); bank = mem('dsp2', 'Y', 0x140)[0]
                out, _ = model.process(packet); stop(0xB5)
                assert [signed(v) for v in mem('dsp2', 'Y', bank, 32)] == out, ('audio', note, block)
                assert mem('dsp2', 'Y', 0x806, 5) == model.s, ('state', note, block)
                if block < 63:
                    stop(e['machine_render'])
            check(f'live note {note} dec {dec} ramp {ramp} rdec {rdec}: 64 blocks exact', True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--firmware', type=Path, required=True, help='stock OS 1.63 image (for its decay table and RAM IDs)')
    ap.add_argument('--kernel-only', action='store_true')
    a = ap.parse_args()
    build = json.loads((a.build / 'build.json').read_text())
    build['_dir'] = str(a.build)
    stock_main = img.extract(a.firmware.read_bytes())[0]['MainOS']

    def decay(k):                             # the handler's lookup for knob k: decay[(k << 7) >> 5]
        o = 0x4BF94 + 16 * k
        return int.from_bytes(stock_main[o:o + 4], 'big')

    kernel_tests(build, decay)
    if not a.kernel_only:
        booted(build, stock_main)
    print('ALL PASS')


if __name__ == '__main__':
    main()
