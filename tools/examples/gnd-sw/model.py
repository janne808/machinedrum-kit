"""Bit-exact integer model of GND-SW's DSP render (docs/14-custom-machine-reference.md).

Packet: +1 phase step, +2 decay, +3 ramp, +4 ramp decay.
State:  +6 ramp envelope, +7 phase, +8 unused, +9/+A amplitude envelope (low, high).
"""
Q = 1 << 23


def note_step(n):
    """The handler's phase step for MIDI note n (pitch.inc)."""
    return round(440 * 2 ** ((n - 69) / 12) * Q / 44100)


class Model:
    def __init__(self):
        self.s = [0] * 5                      # +6..+A

    def process(self, packet, trig=False):
        """One 32-sample block. Returns (output samples, oscillator scratch X:0..31)."""
        if trig:
            self.s = [Q - 8, 0, 0, 0, Q - 8]
        pitch, decay, ramp, rdec = packet
        renv, phase, unused, lo, hi = self.s
        step = min(Q // 2 - 1, pitch + ((ramp * renv * 4) >> 23))
        renv = renv * rdec >> 23
        env = (hi << 24) | lo
        out, raw = [], []
        for _ in range(32):
            v = 2 * phase - Q
            if phase == 0:
                v = 0
            elif phase < step:
                u = (step - phase) * Q // step
                v += (u * u) >> 23
            elif Q - phase < step:
                u = (step - (Q - phase)) * Q // step
                v -= (u * u) >> 23
            osc = v >> 2
            raw.append(osc)
            out.append(osc * env >> 47)
            env = env * decay >> 23
            phase = (phase + step) & (Q - 1)
        self.s = [renv, phase, unused, env & 0xFFFFFF, env >> 24]
        return out, raw
