# NFX-4P example

NFX-4P is a Moog-style 4-pole ladder filter (Kocmoc uLADR) on the previous track's raw voice, with a trig-driven AD envelope on the cutoff and an envelope or gate VCA. It is in a new **NFX** family on the stock OS 1.63 image: firmware ID
14, DSP type 15, code at `P:0x12D000` (700 words). On the first track
it outputs silence.

The build uses the
[E12 layout](../../../docs/12-packing-firmware.md#removing-e12-instead-memory-without-a-reservation):
the 16 E12 machines make room for the code, and sample memory, the ROM budget
and RAM recording and playback stay stock. The DSP code is the code that has run
on hardware in custom builds, without one input branch that served a two-track
machine the kit does not have.

| Knob | Default | Meaning |
| --- | ---: | --- |
| FREQ | 64 | Cutoff, the same curve as NFX-SV, `0.001 + 2.25·c⁴` |
| RESO | 0 | Feedback `fb = 5·RESO/127`; self-oscillates above about 100 |
| MODE | 0 | 0–63 the 4th pole (24 dB/oct low-pass), 64–127 the 2nd pole (12 dB/oct) |
| ENVA | 64 | Envelope depth on the cutoff: 64 none, higher sweeps up, lower down |
| ATK | 0 | Attack, charging toward twice full scale and ending at full scale: 0 instant, about 1.6 ms to 10 s |
| DEC | 64 | Exponential decay, same knob-to-time law |
| GAIN | 64 | Drive into the ladder: passband gain `9.6·(GAIN/127)⁴`, 0.62 at 64 |
| VCA | 127 | 127 bypass; 64–126 the envelope sets the output level; 0–63 a gate of `VCA + 1` 1/128 notes at the current tempo |

The knobs, envelope, VCA and tanh table are shared with the other NFX filter;
see [common/nfx](../common/nfx/README.md).

## How it works

Two semi-implicit Euler steps per sample of the four-pole cascade:

    dt = cutoff/2 (≤ 0.55), error = 1 + 2·dt
    p0 += dt·(tanh(in − error·fb·p3) − p0)
    p1 += dt·(p0 − p1);  p2 += dt·(p1 − p2);  p3 += dt·(p2 − p3)
    out = 2.4 · {p3 | p1}

The poles are Q23 values, each update a `mac` pair with convergent rounding. The
tanh argument goes through the accumulator limiter, so arguments of 8 or more
take the last table entry; the worst error against tanh is 55 LSB
(−103.7 dBFS). `ladder_control` calls `svf_control`, then replaces the RESO word
(fb/8) and the GAIN word (gain⁴/4, from `gain.inc`).

| File | Contents |
| --- | --- |
| `ladder.s` | DSP2 code |
| `ladder_control.s`, `generate_gain.py`, `gain.inc` | The ladder's handler and its GAIN table |
| `model.py` | Bit-exact integer model of the ladder and its control words (state from `Y:S+9`: p0–p3, four unused words, then the envelope and VCA words) |
| `build.py`, `verify.py` | Build and verification |
| `../common/nfx/` | The shared handler `svf_control`, its tables, the tanh table, and the shared models and verification steps |

```sh
python3 build.py --firmware elektron_sps1-1uw_os1.63.bin \
    --asm /path/to/dsp56300-asm --output out/ [--version 163L]
python3 verify.py --build out/
```

What the build changes:

| Where | Change |
| --- | --- |
| MainOS family table | E12 removed; copied to flash `0xFF000` with `NFX` appended as the tenth family (menu `[NFX-4P]` at `0xFF060`); the eight table references repointed |
| MainOS ID table | ID 14 → the NFX-4P descriptor (`0xFF080`); E12 IDs 48–63 → the empty machine |
| DSP2 dispatch | Type 15 → NFX-4P entries; E12 types 49–64 → the fallback |
| DSP2 upload | E12's 42 sample sections removed; P section at `0x12D000`; the tanh table as an X section at `0x280` |
| Flash `0xFF200` | The shared `svf_control` with its tables, then `ladder_control` and the gain table (2,092 bytes), linked at `0x100FF200` |

## Cost

Emulator cycles per block (mean, sine input):

| Scenario | Cycles |
| --- | ---: |
| Constant cutoff, VCA bypassed | 2,931 |
| Envelope on the cutoff | 3,287 |
| Envelope on the cutoff and envelope VCA | 3,469 |

**On hardware**, with the same DSP code in a custom build (a generator, 14
instances with the envelope on the cutoff and the envelope VCA, and a load meter
on track 16): about 3,980 cycles per instance, about 460 more than the emulator. The meter first underran at 23 steps of 512 cycles: 11,300–11,800 cycles were still free, so a 15th instance fits. It is above the ~3,100-cycle per-track floor, so its cost
adds up in a full kit (see
[budgeting](../../../docs/13-dsp-programming.md#budgeting)).

## Verified

`verify.py`, in the emulator:

- **Kernel** (`md-kernel`, both DSP engines, registers randomized and memory
  poisoned on every call, the tanh table loaded at `X:0x280`): 50 cases, every
  output sample and state word equal to `model.py`. They cover impulses in five
  modes × three resonances, enveloped sweeps, a 2,500-block random stress run,
  every value of every knob with trigs, all VCA zones, a full-scale square at
  maximum drive (taking the tanh clamp path), zero input, and tracks 0 and 15.
- **Booted image:**
  - the family table (E12 out, NFX in), the NFX menu, the ID, dispatch cells and
    program;
  - the tanh table in internal X;
  - stock sample budgets;
  - the browser shows NFX with one entry and assigns ID 14;
  - the gate length follows the tempo;
  - with GND-NS on track 1 and NFX-4P on track 2, every output sample and state
    word matches the model for 48 live blocks at each of three settings.

The image built here has not been flashed to hardware.
