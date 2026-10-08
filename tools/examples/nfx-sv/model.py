"""Bit-exact integer model of NFX-SV. The control words, envelope and VCA are in
../common/nfx/nfx.py; the state list matches the DSP state block from Y:S+9."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common' / 'nfx'))
from nfx import Q, TANH, block_vca, envelope, sat, signed, toward0, trigger  # noqa: E402

controls = __import__('nfx').controls_sv


def tanh_band(bacc):
    """Band saturator (K/2) tanh(2m/K) of the 48-bit band accumulator: |4 bacc|
    through the limiter, 10-bit index, 13-bit fraction, interpolation / 32, sign."""
    m = abs(bacc) * 4
    y1 = m >> 24 if m < 1 << 47 else Q - 1
    i, fr = y1 >> 13, (y1 & 0x1FFF) << 10
    v = (2 * fr * TANH[i + 1] - 2 * fr * TANH[i] + (TANH[i] << 24)) >> 29
    return v if bacc >= 0 else -v


class SV:
    """State: bp lp be le hp stage level dt trigs peaks ef gs gc (Y:S+9..+$15)."""
    def __init__(self, track=1):
        self.track = track
        self.s = [0] * 13

    def process(self, c, src, flags=0):
        if flags & 1:
            self.s = [0] * 13
        if flags & 2:
            trigger(self.s, c[7], 5, 6, 8, 10, 12)
        if not self.track:
            return [0] * 32
        base, fb, mode, depth, atk, dec, gain, vca = c
        bp, lp, be, le, hp, stage, level, dt, trigs, peaks, ef, gs, gc = self.s
        active = stage in (1, 2)
        stage, level, ef, peaked = envelope(stage, level, ef, atk, dec)
        peaks = (peaks + peaked) & 0xFFFFFF
        if active and depth:
            dt1 = max(2097, min(0x480000, base + (depth * level >> 23)))
            ddt = toward0(dt1 - dt)
            ramp = [dt + (n + 1) * ddt for n in range(32)]
            dt += 32 * ddt
        else:
            dt = base
            ramp = [base] * 32
        out = []
        for d, x in zip(ramp, src):
            x = (signed(x) * gain) >> 23
            for _ in range(2):
                hp = sat(((x - lp) * Q - fb * bp) // Q)
                bacc = (bp << 24) + be + 4 * d * hp
                be = bacc & 0xFFFFFF
                bp = tanh_band(bacc)
                lacc = (lp << 24) + le + 4 * d * bp
                lp = sat(lacc >> 24)
                le = lacc & 0xFFFFFF
            lo, hi = (bp, hp) if mode & Q else (lp, bp)
            mix = mode & (Q - 1)
            if mode == 0xFFFFFF:
                lo, mix = hi, 0
            out.append(sat(((lo * Q + (hi - lo) * mix) * 32) >> 23))
        if vca:
            out, gs, gc = block_vca(out, vca, level, gs, gc)
        self.s = [bp, lp, be, le, hp, stage, level, dt, trigs, peaks, ef, gs, gc]
        return out


