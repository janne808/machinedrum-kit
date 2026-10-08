"""Shared by the NFX-SV and NFX-4P examples: svf_control's control words, the
block-rate AD envelope and block VCA (bit-exact integer models), the tanh table,
and the verification steps both examples run.

Words are Q23 integers (full scale 2^23).
"""
import json
import math
import random
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[2]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'emulator')]

from generate_tables import TABLES, TANH, TANH_ADDRESS  # noqa: E402

LABELS = ['FREQ', 'RESO', 'MODE', 'ENVA', 'ATK', 'DEC', 'GAIN', 'VCA']
DEFAULTS = [64, 0, 0, 64, 0, 64, 64, 127]
Q = 1 << 23
VCA_SLEW = 190218                     # full scale in 1 ms, per sample
VCA_STEP32 = 32 * VCA_SLEW


def sat(v):
    return max(-Q, min(Q - 1, v))


def signed(w):
    return (w & 0x7FFFFF) - (w & 0x800000)


# ---- control words (control.s) ------------------------------------------------------

def knob(raw):
    """The handler's rounding of a raw knob word (about value << 7) to 0..127."""
    return min(127, (raw + 64) >> 7)


def lookup(name, k):
    """65-entry table lookup; odd knobs average their neighbours (floored)."""
    t = TABLES[name]
    if k == 127:
        return t(127)
    v = t(2 * (k // 2))
    return (v + t(min(127, 2 * (k // 2) + 2))) // 2 if k & 1 else v


def attack_word(k):
    if not k:
        return Q - 1
    v = lookup('svf_attack', k)
    return v >> 8 if v >= Q else v | Q        # bit 23: mantissa is alpha * 2^31


def vca_word(k, tempo=2880):
    """127 bypass (0), 64-126 envelope (1), 0-63 a gate of k+1 1/128 notes
    (tempo = BPM * 24, the OS 1.63 global the handler reads)."""
    if k == 127:
        return 0
    if k >= 64:
        return 1
    return max(2, ((((k + 1) * 128) * 44100) >> 10) * 360 // tempo)


def controls_sv(freq=64, reso=0, mode=0, enva=64, atk=0, dec=64, gain=64, vca=127, tempo=2880):
    mode_word = mode << 17 if mode < 64 else (0xFFFFFF if mode == 127 else Q + (mode - 64) * 133152)
    return [lookup('svf_cutoff', freq), lookup('svf_damping', reso), mode_word, lookup('svf_depth', enva),
            attack_word(atk), Q - 1 if not dec else lookup('svf_time', dec), gain * 4096, vca_word(vca, tempo)]


# ---- shared blocks ------------------------------------------------------------------

def pow32(k):
    """1 - (1 - k)^32: five rounds of e <- 2e - e^2 on a 48-bit accumulator
    (1.0 = 2^47), squaring the limited high word."""
    e = k
    for _ in range(5):
        x = min(e >> 24, Q - 1)
        e = 2 * e - 2 * x * x
    return min(e >> 24, Q - 1)


def toward0(d):
    return d >> 5 if d >= 0 else -((-d) >> 5)


def rnd(acc):
    """DSP56300 convergent rounding of a 48-bit accumulator to its high word."""
    hi, lo = acc >> 24, acc & 0xFFFFFF
    return hi + 1 if lo > 0x800000 or (lo == 0x800000 and hi & 1) else hi


def envelope(stage, level, ef, atk, dec):
    """One block of the AD envelope. Returns (stage, level, ef, peaked)."""
    shift = 8 if atk & Q else 0
    atk &= Q - 1
    if stage == 1:
        e = pow32((atk << 24) >> shift)
        acc = (level << 24) + ef + 2 * e * (Q - 1 - level) + 2 * e * (Q - 1)
        if acc >= (Q - 1) << 24:
            return 2, Q - 1, 0, True
        return 1, acc >> 24, acc & 0xFFFFFF, False
    if stage == 2:
        e = pow32(dec << 24)
        acc = (level << 24) + ef - 2 * e * level
        if acc <= 128 << 24:
            return 0, 0, 0, False
        return 2, acc >> 24, acc & 0xFFFFFF, False
    return stage, level, ef, False


def block_vca(out, vca, level, gs, gc):
    """One linear gain ramp per block toward the envelope level (vca = 1) or the
    gate (full scale while more than half a block of the count remains),
    clamped to full scale per ms; snaps within 32 LSB; unity at full scale."""
    if vca == 1:
        target = level
    else:
        target = Q - 1 if gc > 16 else 0
        gc = max(0, gc - 32)
    d = max(-VCA_STEP32, min(VCA_STEP32, target - gs))
    dg = toward0(d)
    if not (dg == 0 and gs == Q - 1):
        out = [(y * (gs + (i + 1) * dg)) >> 23 for i, y in enumerate(out)]
    end = gs + 32 * dg
    return out, (target if abs(target - end) < 32 else end), gc


def trigger(state, vca, stage_at, level_at, trigs_at, ef_at, gc_at):
    state[stage_at] = 1
    state[trigs_at] = (state[trigs_at] + 1) & 0xFFFFFF
    state[level_at] = 0
    state[ef_at] = 0
    state[gc_at] = vca & 0xFFFFFF


def sine(amp, hz, block):
    return [round(amp * Q * math.sin(2 * math.pi * hz * (block * 32 + j) / 44100)) for j in range(32)]


# ---- verification ---------------------------------------------------------------------

KERNEL = TOOLS / 'emulator' / 'build' / 'md-kernel'


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)
    if not ok:
        raise SystemExit(1)


def cases():
    """[(case name, track, [(flags, knobs, neighbour samples)])]."""
    out = []
    for mode in (0, 32, 64, 96, 127):
        for reso in (0, 64, 127):
            out.append((f'impulse mode {mode} reso {reso}', 1,
                        [(1 if b == 0 else 0, [80, reso, mode, 64, 0, 64, 96, 127],
                          [Q // 2 if b == 0 and j == 0 else 0 for j in range(32)]) for b in range(96)]))
            out.append((f'envelope mode {mode} reso {reso}', 1,
                        [(3 if b == 0 else (2 if b == 60 else 0), [50, reso, mode, 127, 10, 40, 110, 127],
                          sine(.8, 220, b)) for b in range(160)]))
    rnd = random.Random(4)

    def stress():
        k = [rnd.randrange(128) for _ in range(8)]
        for b in range(2500):
            if rnd.random() < .06:
                k[rnd.randrange(8)] = rnd.randrange(128)
            x = [rnd.choice((-Q, Q - 1)) for _ in range(32)] if b % 5 == 0 else sine(1, rnd.choice((55, 440, 3000)), b)
            yield (1 if b == 0 else 0) | (2 if rnd.random() < .04 else 0), list(k), x
    out.append(('random stress', 1, list(stress())))
    for knob in range(8):
        out.append((f'every value of {["FREQ", "RESO", "MODE", "ENVA", "ATK", "DEC", "GAIN", "VCA"][knob]}', 1,
                    [((1 if b == 0 else 0) | (2 if b % 16 == 0 else 0),
                      [b if i == knob else d for i, d in enumerate([64, 90, 0, 100, 10, 40, 100, 90])], sine(.7, 150, b))
                     for b in range(128)]))
    for vca in (0, 20, 63, 64, 100, 126, 127):
        out.append((f'VCA {vca}', 1, [((1 if b == 0 else 0) | (2 if b % 50 == 0 else 0), [90, 100, 32, 100, 16, 40, 127, vca],
                                       sine(.9, 330, b)) for b in range(200)]))
    square = lambda b: [Q - 1 if math.sin(2 * math.pi * 1280 * (b * 32 + j) / 44100) >= 0 else -Q for j in range(32)]
    out.append(('full-scale square, maximum drive and resonance', 1,
                [((1 if b == 0 else 0) | (2 if b % 20 == 0 else 0), [89, 127, 0, 64, 0, 115, 127, 127], square(b)) for b in range(60)]))
    out.append(('zero input', 1, [(3 if b == 0 else 0, [127, 127, 0, 127, 16, 32, 127, 127], [0] * 32) for b in range(512)]))
    for track in (0, 15):
        out.append((f'track {track}', track, [(1 if b == 0 else 0, [64, 100, 0, 64, 0, 64, 127, 127], sine(1, 110, b)) for b in range(64)]))
    return out


def kernel_tests(build, Model, controls, nstate, lod):
    """Run cases() in md-kernel, both engines, with the tanh table loaded into
    internal X; every output sample and state word must equal the model."""
    from mdkit import image as img
    from mdkit.machine import program_from_lod
    m = build['machine']
    words, entries, _ = program_from_lod(lod.read_text(), m['bank'], 0x1000)
    worst, all_cases = 0, cases()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'tanh.bin').write_bytes(img.words_le(TANH))
        (tmp / 'program.bin').write_bytes(img.words_le(words))
        for name, track, blocks in all_cases:
            model, rec, expected = Model(track), [], []
            for flags, knobs, x in blocks:
                c = controls(*knobs)
                y = model.process(c, [v & 0xFFFFFF for v in x], flags)
                rec += [flags] + [v & 0xFFFFFF for v in c] + [v & 0xFFFFFF for v in x]
                expected.append((y, list(model.s)))
            (tmp / 'in.bin').write_bytes(struct.pack(f'<{len(rec)}I', *rec))
            for engine in ('jit', 'interpreter'):
                r = subprocess.run([str(KERNEL), '--program', str(tmp / 'program.bin'), '--bank', hex(m['bank']),
                                    '--init', str(entries[0]), '--update', str(entries[1]), '--render', str(entries[2]),
                                    '--input', str(tmp / 'in.bin'), '--output', str(tmp / 'out.bin'),
                                    '--packet', '8', '--track', str(track), '--engine', engine,
                                    '--load', f'X:{TANH_ADDRESS:#x}:{tmp / "tanh.bin"}'], capture_output=True, text=True)
                if r.returncode:
                    raise SystemExit(f'FAIL kernel {name} {engine}: {r.stderr.strip()}')
                got = struct.unpack(f'<{(tmp / "out.bin").stat().st_size // 4}I', (tmp / 'out.bin').read_bytes())
                for i, (y, s) in enumerate(expected):
                    b = got[i * 128:(i + 1) * 128]
                    if list(b[:32]) != [v & 0xFFFFFF for v in y] or list(b[32 + 9:32 + 9 + nstate]) != [v & 0xFFFFFF for v in s]:
                        raise SystemExit(f'FAIL kernel {name} ({engine}): block {i} differs from the model')
                if engine == 'jit':
                    worst = max(worst, int(re.search(r'worst_block_cycles=(\d+)', r.stdout)[1]))
    check(f'kernel {m["name"]}: {len(all_cases)} cases exact in both engines, guards pass, worst {worst} cycles per block', True)


def booted(build, image, Model, nstate, signed_words):
    """Boot the image: registration, the tanh table, stock budgets, front-panel
    selection, the tempo-synced gate, and live audio and state equal to the model
    (GND-NS on track 1, the machine on track 2) at three settings."""
    from monitor import MonitorClient
    m = build['machine']
    e = m['entries']
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
        t, menu = m['family_table_cpu'], m['menu_cpu']
        names = [bytes(mem('coldfire', 'B', t + 8 * i, 4)).rstrip(b'\0').decode() for i in range(10)]
        check(f'families {names}', names == ['GND', 'TRX', 'EFM', 'P-I', 'INP', 'MID', 'CTR', 'ROM', 'RAM', 'NFX']
              and u32(t + 76) == menu and u32(t + 80) == 0)
        check(f'NFX menu [{m["name"]}] and ID {m["id"]}', [u32(menu), u32(menu + 4)] == [m['descriptor_cpu'], 0]
              and u32(0x252092 + 4 * m['id']) == m['descriptor_cpu'])
        check(f'dispatch type {m["dsp_type"]}', [mem('dsp2', 'Y', x + m['dsp_type'])[0] for x in (0x145AF5, 0x145BB6, 0x145C77)]
              == [e['machine_init'], e['machine_update'], e['machine_render']])
        check('tanh table in DSP2 internal X:0x280', mem('dsp2', 'X', TANH_ADDRESS, len(TANH)) == TANH)
        check('sample budgets stock', (u32(0x29E9F0), u32(0x29F6DE)) == (0x140000, 0x15F400))

        assign(0, 1)
        press(0x22, 0x40); press(0x23, 0x20); press(0x24, 0x10); press(0x23, 0x40); press(0x24, 0x08)
        for _ in range(10):
            press(0x23, 0x40)
        check('browser on NFX (family 9) with 1 entry', u32(0x28B72C) == 9 and u32(0x28C2D8) == 1)
        press(0x23, 0x80); press(0x24, 0x08)
        check(f'front panel assigns ID {m["id"]}', u32(0x7001AA) == m['id'])
        for _ in range(3):
            press(0x23, 0x10)

        tempo = u32(0x100150C)
        assign(0, 2)
        assign(1, m['id'])
        for knobs in ([60, 100, 0, 110, 8, 60, 100, 100], [90, 127, 64, 64, 0, 64, 127, 127], [40, 80, 0, 20, 30, 90, 80, 10]):
            for i, v in enumerate(knobs):
                cmd(f'midi 0xb0 {40 + i} {v}')
            go(22050); cmd('midi 0x90 36 120'); cmd('midi 0x90 38 120'); go(2205)
            stop(e['machine_render'])
            model = Model(1)
            model.s = [signed(v) if i in signed_words else v for i, v in enumerate(mem('dsp2', 'Y', 0x849, nstate))]
            if knobs[7] < 64:
                gate = mem('dsp2', 'Y', 0x848)[0]
                check(f'VCA {knobs[7]}: gate of {gate} samples at {tempo / 24:g} BPM', gate == vca_word(knobs[7], tempo))
            for block in range(48):
                c = mem('dsp2', 'Y', 0x841, 8)
                c[3] = signed(c[3])           # envelope depth is signed; mode and attack carry bit-23 flags
                bank = mem('dsp2', 'Y', 0x140)[0]
                y = model.process(c, mem('dsp2', 'Y', bank ^ 0x20, 32), 0)
                stop(0xB5)
                assert [signed(v) for v in mem('dsp2', 'Y', bank, 32)] == y, (knobs, block)
                assert mem('dsp2', 'Y', 0x849, nstate) == [v & 0xFFFFFF for v in model.s], (knobs, block)
                if block < 47:
                    stop(e['machine_render'])
            check(f'knobs {knobs}: 48 live blocks exact (peak {max(abs(v) for v in y)})', True)


def run(build_dir, image_name, Model, controls, nstate, lod_name, signed_words, kernel_only=False, extra=None):
    build = json.loads((Path(build_dir) / 'build.json').read_text())
    lod = Path(build_dir) / 'work' / 'dsp' / lod_name
    kernel_tests(build, Model, controls, nstate, lod)
    if extra:
        extra()
    if not kernel_only:
        booted(build, Path(build_dir) / image_name, Model, nstate, signed_words)
    print('ALL PASS')
