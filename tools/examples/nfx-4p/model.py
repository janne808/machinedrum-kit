"""Bit-exact integer model of NFX-4P. The shared control words, envelope and VCA
are in ../common/nfx/nfx.py; the state list matches the DSP state block from
Y:S+9."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common' / 'nfx'))
from nfx import Q, TANH, block_vca, controls_sv, envelope, rnd, sat, signed, toward0, trigger  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_gain import gain as gain_entry  # noqa: E402


def gain_word(k):
    """ladder_control's GAIN lookup: 65 entries, odd knobs averaged (floored)."""
    if k == 127:
        return gain_entry(127)
    v = gain_entry(2 * (k // 2))
    return (v + gain_entry(min(127, 2 * (k // 2) + 2))) // 2 if k & 1 else v


def controls(freq=64, reso=0, mode=0, enva=64, atk=0, dec=64, gain=64, vca=127, tempo=2880):
    c = controls_sv(freq, 0, mode, enva, atk, dec, 64, vca, tempo)
    c[1] = (reso * 5242880 + 63) // 127          # fb/8, fb = 5 RESO/127
    c[6] = gain_word(gain)                       # gain^4 / 4
    return c


DT_MIN, DT_MAX, OUT_SCALE = 4194, 4613734, 5033165      # dt clamp; 0.6 (x 8 = 4.8)
CLAMPS = [0]                                            # lookups that hit the limiter


def tanh_ladder(acc):
    """tanh of the argument accumulator (arg/16, 48-bit): |2 acc| through the
    limiter, 10-bit index, 13-bit fraction, interpolation floored, sign."""
    m = abs(2 * acc)
    y1 = m >> 24 if m < 1 << 47 else Q - 1
    CLAMPS[0] += m >= 1 << 47
    i, fr = y1 >> 13, (y1 & 0x1FFF) << 10
    t = (2 * fr * TANH[i + 1] - 2 * fr * TANH[i] + (TANH[i] << 24)) >> 24
    return -t if acc < 0 else t


def c2_of(dt, fb8):
    return (2 * dt * fb8 + (fb8 << 23)) >> 24          # (fb/8) dt + fb/16


class Ladder:
    """State: p0 p1 p2 p3, four unused, stage level dt trigs peaks ef gs gc
    (Y:S+9..+$18)."""
    def __init__(self, track=1):
        self.track = track
        self.s = [0] * 16

    def process(self, c, src, flags=0):
        if flags & 1:
            self.s = [0] * 16
        if flags & 2:
            trigger(self.s, c[7], 8, 9, 11, 13, 15)
        if not self.track:
            return [0] * 32
        base, fb8, mode, depth, atk, dec, g, vca = c
        ph = self.s[0:4]
        stage, level, dt_last, trigs, peaks, ef, gs, gc = self.s[8:16]
        active = stage in (1, 2)
        stage, level, ef, peaked = envelope(stage, level, ef, atk, dec)
        peaks = (peaks + peaked) & 0xFFFFFF
        if active and depth:
            dt1 = min(DT_MAX, max(DT_MIN, (2 * ((base << 24) + 2 * depth * level)) >> 24))
            ddt = toward0(dt1 - dt_last)
            dt = dt_last + 32 * ddt
            c20 = c2_of(dt_last, fb8)
            dc2 = toward0(c2_of(dt, fb8) - c20)
            ramp = [(dt_last + (n + 1) * ddt, c20 + (n + 1) * dc2) for n in range(32)]
        else:
            dt = min(DT_MAX, 2 * base)
            ramp = [(dt, c2_of(dt, fb8))] * 32
        out = []
        for (d, c2), x in zip(ramp, src):
            in16 = (2 * g * signed(x)) >> 24
            for _ in range(2):
                t = tanh_ladder((in16 << 24) - 2 * c2 * ph[3])
                ph[0] = rnd((ph[0] << 24) + 2 * d * t - 2 * d * ph[0])
                for k in (1, 2, 3):
                    ph[k] = rnd((ph[k] << 24) + 2 * d * ph[k - 1] - 2 * d * ph[k])
            v = (ph[1] if mode & Q else ph[3]) >> 1
            out.append(sat((2 * v * OUT_SCALE * 8) >> 24))
        if vca:
            out, gs, gc = block_vca(out, vca, level, gs, gc)
        self.s = ph + [0, 0, 0, 0] + [stage, level, dt, trigs, peaks, ef, gs, gc]
        return out


