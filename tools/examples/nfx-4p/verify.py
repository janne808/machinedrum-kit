#!/usr/bin/env python3
"""Verify an NFX-4P build (build.py output) in the kit's emulator.

1. Kernel: md-kernel runs the DSP code outside the firmware in both DSP engines,
   with poisoned registers and memory and the tanh table loaded at X:0x280.
   Every output sample and state word must equal model.py: impulses in every
   mode and resonance, enveloped sweeps, a long random stress run, every value
   of every knob, all VCA zones, a full-scale square at maximum drive, zero
   input, and tracks 0 and 15.
2. Booted image: the family table (E12 out, NFX in), the NFX menu, ID, dispatch
   and program, the tanh table in internal X, stock sample budgets, front-panel
   selection, the gate length following the tempo, and live audio and state
   equal to the model at three settings.

The steps are in ../common/nfx/nfx.py, shared with the other NFX filter.

  python3 verify.py --build out/ [--kernel-only]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / 'common' / 'nfx'), str(HERE)]

import nfx  # noqa: E402
import model  # noqa: E402


def clamp_path():
    """The tanh limiter path must have run (a full-scale square at maximum drive)."""
    nfx.check(f'kernel: the tanh clamp path ran ({model.CLAMPS[0]} clamped lookups)', model.CLAMPS[0] >= 100)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--kernel-only', action='store_true')
    a = ap.parse_args()
    # State words read back as signed values: the four poles.
    nfx.run(a.build, 'machinedrum-nfx-4p.bin', model.Ladder, model.controls, 16, 'ladder.lod', (0, 1, 2, 3),
            a.kernel_only, extra=clamp_path)


if __name__ == '__main__':
    main()
