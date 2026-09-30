#!/usr/bin/env python3
"""Write pitch.inc: the MIDI-note phase steps for GND-SW's ColdFire handler.
Equal temperament, A4 = 440 Hz, phase period 2^23, Fs = 44100."""
from pathlib import Path


def content():
    return ('| MIDI equal temperament, A4=440 Hz, phase period 2^23, Fs=44100.\n' +
            ''.join(f'        .long {round(440 * 2 ** ((n - 69) / 12) * (1 << 23) / 44100)} | MIDI {n}\n'
                    for n in range(128)))


if __name__ == '__main__':
    Path(__file__).with_name('pitch.inc').write_text(content())
