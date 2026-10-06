#!/usr/bin/env python3
"""Verify an NFX-GN build (build.py output) in the kit's emulator.

1. Kernel: md-kernel runs the DSP code outside the firmware in both DSP
   engines with poisoned registers and memory; output must equal model.py for
   random neighbour input and gains, and be silent on track 0.
2. Booted image: relocated family table without E12, stock sample budgets, E12 IDs and
   dispatch removed, registration, RAM machines kept,
   front-panel selection, live audio equal to the model at six GAIN settings,
   and silence on track 0.

  python3 verify.py --build out/ [--kernel-only]
"""
import argparse
import json
import random
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
from mdkit.machine import program_from_lod  # noqa: E402
from model import packet, render, signed  # noqa: E402

KERNEL = TOOLS / 'emulator' / 'build' / 'md-kernel'


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)
    if not ok:
        raise SystemExit(1)


def kernel_tests(build):
    m = build['machine']
    lod = (Path(build['_dir']) / 'work' / 'dsp' / 'machine.lod').read_text()
    words, entries, _ = program_from_lod(lod, m['bank'], 0x1000)
    rnd = random.Random(15)
    blocks = []
    for b in range(512):
        raw = rnd.choice([0, 63, 64, 128, 8192, 16256, rnd.randrange(16257)])
        x = [rnd.choice([rnd.randrange(-(1 << 23), 1 << 23), (1 << 23) - 1, -(1 << 23), 0]) for _ in range(32)]
        blocks.append((b % 50 == 0, packet(raw), x))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'program.bin').write_bytes(img.words_le(words))
        rec = []
        for trig, g, x in blocks:
            rec += [(1 if not rec else 0) | (2 if trig else 0), g & 0xFFFFFF] + [v & 0xFFFFFF for v in x]
        (tmp / 'in.bin').write_bytes(struct.pack(f'<{len(rec)}I', *rec))
        for track in (1, 15, 0):
            for engine in ('jit', 'interpreter'):
                r = subprocess.run([str(KERNEL), '--program', str(tmp / 'program.bin'), '--bank', hex(m['bank']),
                                    '--init', str(entries[0]), '--update', str(entries[1]), '--render', str(entries[2]),
                                    '--input', str(tmp / 'in.bin'), '--output', str(tmp / 'out.bin'),
                                    '--packet', '1', '--track', str(track), '--engine', engine],
                                   capture_output=True, text=True)
                if r.returncode:
                    raise SystemExit(r.stderr)
                data = (tmp / 'out.bin').read_bytes()
                got = struct.unpack(f'<{len(data) // 4}I', data)
                for i, (_, g, x) in enumerate(blocks):
                    want = [v & 0xFFFFFF for v in render(x, g & 0xFFFFFF, track)]
                    if list(got[i * 128:i * 128 + 32]) != want:
                        raise SystemExit(f'FAIL kernel track {track} {engine}: block {i} differs from the model')
                cycles = re.search(r'worst_block_cycles=(\d+)', r.stdout)[1]
                check(f'kernel track {track} ({engine}): {len(blocks)} blocks exact, guards pass' + (f', worst {cycles} cycles' if engine == 'jit' else ''), True)


def booted(build):
    from monitor import MonitorClient
    m = build['machine']
    e = m['entries']
    image = Path(build['_dir']) / 'machinedrum-nfx-gn.bin'
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

        def assign(t, ident):
            cmd(f'midi 0xf0 0 0x20 0x3c 2 0 0x5b {t} {ident} 0 2 0xf7'); go(22050)

        def press(row, mask):
            cmd(f'panel {row} {mask}'); go(2205); cmd(f'panel {row} 0'); go(4410)

        def stop(pc):
            cmd('cpu dsp2'); bp = cmd(f'break {pc}')['id']; r = cmd('continue 44100'); cmd(f'delete {bp}')
            assert r.get('reason') == 'breakpoint' and r['hit_id'] == bp, r

        check('boot', cmd('boot')['reason'] == 'boot_ready'); go(330750)
        t = m['family_table_cpu']
        check('eight references point at the relocated family table',
              all(u32(a) == t for a in (0x22C1A8, 0x23065A))
              and all(u32(a) == t + 4 for a in (0x22C210, 0x231A60, 0x231ABA, 0x231B10, 0x231F22, 0x235368)))
        names = [bytes(mem('coldfire', 'B', t + 8 * i, 4)).rstrip(b'\0').decode() for i in range(10)]
        check(f'families {names}', names == ['GND', 'TRX', 'EFM', 'P-I', 'INP', 'MID', 'CTR', 'ROM', 'RAM', 'NFX']
              and u32(t + 80) == 0 and u32(t + 84) == 0)
        check('sample budgets stock', (u32(0x29E9F0), u32(0x29F6DE)) == (0x140000, 0x15F400))
        fallback = [mem('dsp2', 'Y', x)[0] for x in (0x145AF5, 0x145BB6, 0x145C77)]
        check('E12 IDs 48-63 empty, DSP types 49-64 on the fallback',
              all(u32(0x252092 + 4 * i) == 0x24EF54 for i in range(48, 64))
              and all(mem('dsp2', 'Y', x + i + 1)[0] == f for i in range(48, 64) for x, f in zip((0x145AF5, 0x145BB6, 0x145C77), fallback)))
        check('NFX menu and ID 15', u32(t + 76) == m['menu_cpu'] and u32(m['menu_cpu']) == m['descriptor_cpu']
              and u32(m['menu_cpu'] + 4) == 0 and u32(0x252092 + 60) == m['descriptor_cpu'])
        check('RAM machines still registered', all(u32(0x252092 + 4 * i) != 0x24EF54 for i in (160, 161, 162, 163, 165, 166, 167, 168)))
        check('dispatch type 16', [mem('dsp2', 'Y', x + 16)[0] for x in (0x145AF5, 0x145BB6, 0x145C77)]
              == [e['machine_init'], e['machine_update'], e['machine_render']])
        assign(0, 1)
        press(0x22, 0x40); press(0x23, 0x20); press(0x24, 0x10); press(0x23, 0x40); press(0x24, 0x08)
        for _ in range(10):
            press(0x23, 0x40)
        check('browser on NFX (family 9) with 1 entry', u32(0x28B72C) == 9 and u32(0x28C2D8) == 1)
        press(0x23, 0x80); press(0x24, 0x08)
        check('front panel assigns ID 15', u32(0x7001AA) == 15)
        for _ in range(3):
            press(0x23, 0x10)
        assign(0, 1); assign(1, 15)
        for v in (64, 0, 1, 32, 100, 127):
            cmd(f'midi 0xb0 40 {v}'); go(22050)
            cmd('midi 0x90 36 120'); cmd('midi 0x90 38 120'); go(4410)
            for _ in range(6):
                stop(e['machine_render'])
                assert mem('dsp2', 'Y', 0x142)[0] == 1
                g = mem('dsp2', 'Y', 0x841)[0]; bank = mem('dsp2', 'Y', 0x140)[0]
                x = [signed(w) for w in mem('dsp2', 'Y', bank ^ 0x20, 32)]
                stop(0xB5)
                assert [signed(w) for w in mem('dsp2', 'Y', bank, 32)] == render(x, g, 1), v
            check(f'GAIN {v}: packet 0x{g:06x}, 6 live blocks exact', g == packet(v << 7) and any(x))
        assign(0, 15); cmd('midi 0x90 36 120'); go(4410)
        while True:
            stop(e['machine_render'])
            if mem('dsp2', 'Y', 0x142)[0] == 0:
                break
        bank = mem('dsp2', 'Y', 0x140)[0]; stop(0xB5)
        check('track 0 outputs silence', mem('dsp2', 'Y', bank, 32) == [0] * 32)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--kernel-only', action='store_true')
    a = ap.parse_args()
    build = json.loads((a.build / 'build.json').read_text())
    build['_dir'] = str(a.build)
    kernel_tests(build)
    if not a.kernel_only:
        booted(build)
    print('ALL PASS')


if __name__ == '__main__':
    main()
