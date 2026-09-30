"""Synthetic firmware images for tests that must not depend on Elektron's firmware."""
import os
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mdkit import codec, image as img  # noqa: E402


def dsp_stream(sections, entry=0x24, config=0x123456):
    words = [3, entry, 4, config]
    for space, address, data in sections:
        words += ['PXY'.index(space), address, len(data)] + list(data)
    words += [3, entry]
    return img.words_le(words)


def fake_image(seed=1, version='163 '):
    """8 MiB image with a valid five-blob chain at 0x4000 and an erased tail."""
    rnd = random.Random(seed)
    mainos = bytes(rnd.randrange(4) for _ in range(20000)) + os.urandom(300)
    dsp2 = dsp_stream([('P', 0x0, [rnd.randrange(1 << 24) for _ in range(40)]),
                       ('P', 0x145AF5, [0x10008E] * 193 + [0x10008E] * 193 + [0x10008F] * 193),
                       ('Y', 0x800, [0] * 64)])
    dsp1 = dsp_stream([('P', 0x0, [1, 2, 3])])
    os_a = bytes(512) + b'A' * 100
    os_b = bytes(512) + b'B' * 100
    chain = b''
    for name, payload in (('MainOS', mainos), ('DSP2', dsp2), ('DSP1', dsp1)):
        chain += codec.build_blob(codec.compress(payload))
    chain += version.encode()
    for payload in (os_a, os_b):
        chain += codec.build_blob(codec.compress(payload))
    image = bytearray(b'\xff' * img.IMAGE_SIZE)
    image[:0x4000] = bytes(rnd.randrange(256) for _ in range(0x4000))
    image[0x4000:0x4000 + len(chain)] = chain
    image[0x200000:0x200010] = b'upper flash data'
    return bytes(image), {'MainOS': mainos, 'DSP2': dsp2, 'DSP1': dsp1, 'OS_A': os_a, 'OS_B': os_b}
