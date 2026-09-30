"""Bit-exact integer model of NFX-GN (docs/14-custom-machine-reference.md)."""


def signed(w):
    return (w & 0x7FFFFF) - (w & 0x800000)


def packet(raw):
    """Handler: raw knob word (about value << 7) -> gain/2 in Q23."""
    return 0 if raw < 64 else (raw << 9) & 0xFFFFFFFF


def render(neighbour, gain_word, track):
    """Output block for 32 signed neighbour samples."""
    if track == 0:
        return [0] * 32
    return [max(-(1 << 23), min((1 << 23) - 1, (x * gain_word) >> 22)) for x in neighbour]
