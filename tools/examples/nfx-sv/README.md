# NFX-SV example

NFX-SV is a state-variable filter (uSVF) on the previous track's raw voice, with a trig-driven AD envelope on the cutoff and an envelope or gate VCA. It is in a new **NFX** family on the stock OS 1.63 image: firmware ID
9, DSP type 10, code at `P:0x128000` (785 words). On the first track
it outputs silence.

The build uses the
[E12 layout](../../../docs/12-packing-firmware.md#removing-e12-instead-memory-without-a-reservation):
the 16 E12 machines make room for the code, and sample memory, the ROM budget
and RAM recording and playback stay stock. The DSP code is the code that has run
on hardware in custom builds, without one input branch that served a two-track
machine the kit does not have.

| Knob | Default | Meaning |
| --- | ---: | --- |
| FREQ | 64 | Cutoff, `0.001 + 2.25·c⁴` of the sample rate, about 7 Hz to 16.8 kHz |
| RESO | 0 | Damping, 0.9 down to 0.001 |
| MODE | 0 | 0 low-pass, 64 band-pass, 127 high-pass, linear blends between |
| ENVA | 64 | Envelope depth on the cutoff: 64 none, higher sweeps up, lower down |
| ATK | 0 | Attack, charging toward twice full scale and ending at full scale: 0 instant, about 1.6 ms to 10 s |
| DEC | 64 | Exponential decay, same knob-to-time law |
| GAIN | 64 | Input level: 0 silent, 64 unity, 127 = 1.98× |
| VCA | 127 | 127 bypass; 64–126 the envelope sets the output level; 0–63 a gate of `VCA + 1` 1/128 notes at the current tempo |

The knobs, envelope, VCA and tanh table are shared with the other NFX filter;
see [common/nfx](../common/nfx/README.md).

## How it works

Two semi-implicit SVF steps per sample:

    hp = x − lp − damping·bp
    bp = (K/2)·tanh(2·(bp + 4·dt·hp)/K)      (the tanh table, interpolated)
    lp = lp + 4·dt·bp

The input is scaled by GAIN/64 and by 1/32 of headroom, which the output
undoes. MODE blends low-pass with band-pass (0–63), or band-pass with high-pass
(64–126); 127 is high-pass alone. The band and low-pass integrators keep their
fractions, so slow cutoffs do not stall.

| File | Contents |
| --- | --- |
| `sv.s` | DSP2 code |
| `model.py` | Bit-exact integer model of the filter (state from `Y:S+9`: bp, lp, their fractions, hp, then the envelope and VCA words) |
| `build.py`, `verify.py` | Build and verification |
| `../common/nfx/` | The shared handler `svf_control`, its tables, the tanh table, and the shared models and verification steps |

```sh
python3 build.py --firmware elektron_sps1-1uw_os1.63.bin \
    --asm /path/to/dsp56300-asm --output out/ [--version 163S]
python3 verify.py --build out/
```

What the build changes:

| Where | Change |
| --- | --- |
| MainOS family table | E12 removed; copied to flash `0xFF000` with `NFX` appended as the tenth family (menu `[NFX-SV]` at `0xFF060`); the eight table references repointed |
| MainOS ID table | ID 9 → the NFX-SV descriptor (`0xFF080`); E12 IDs 48–63 → the empty machine |
| DSP2 dispatch | Type 10 → NFX-SV entries; E12 types 49–64 → the fallback |
| DSP2 upload | E12's 42 sample sections removed; P section at `0x128000`; the tanh table as an X section at `0x280` |
| Flash `0xFF200` | The `svf_control` handler and its tables (1,732 bytes), linked at `0x100FF200` |

## Cost

Emulator cycles per block (mean, sine input):

| Scenario | Cycles |
| --- | ---: |
| Constant cutoff, VCA bypassed | 2,572 |
| Envelope on the cutoff | 2,742 |
| Envelope on the cutoff and envelope VCA | 2,924 |

**On hardware**, with the same DSP code in a custom build (a generator, 14
instances with the envelope on the cutoff and the envelope VCA, and a load meter
on track 16): about 3,470 cycles per instance. The meter first underran at 37 steps of 512 cycles: 18,400–18,900 cycles were still free. It is above the ~3,100-cycle per-track floor, so its cost
adds up in a full kit (see
[budgeting](../../../docs/13-dsp-programming.md#budgeting)).

## Verified

`verify.py`, in the emulator:

- **Kernel** (`md-kernel`, both DSP engines, registers randomized and memory
  poisoned on every call, the tanh table loaded at `X:0x280`): 50 cases, every
  output sample and state word equal to `model.py`. They cover impulses in five
  modes × three resonances, enveloped sweeps, a 2,500-block random stress run,
  every value of every knob with trigs, all VCA zones, a full-scale square at
  maximum drive, zero input, and tracks 0 and 15.
- **Booted image:**
  - the family table (E12 out, NFX in), the NFX menu, the ID, dispatch cells and
    program;
  - the tanh table in internal X;
  - stock sample budgets;
  - the browser shows NFX with one entry and assigns ID 9;
  - the gate length follows the tempo;
  - with GND-NS on track 1 and NFX-SV on track 2, every output sample and state
    word matches the model for 48 live blocks at each of three settings.

The image built here has not been flashed to hardware.
