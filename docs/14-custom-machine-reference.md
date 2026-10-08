# Custom machine reference

The kit's machines, as worked examples of the ABI: a generator (GND-SW), a
minimal neighbour effect (NFX-GN) and two neighbour filters (NFX-SV, NFX-4P).
All have complete sources and build scripts
in [`tools/examples`](../tools/examples/). For each: identity, algorithm,
packet and state layout, and cost.

Costs are emulator cycles per 32-sample block (budget 73,728; an idle track
costs 3,438). The emulator charges no memory wait states, so hardware costs
are higher.

| Machine | ID / DSP type | Family | DSP bank | Kind | Cost |
| --- | --- | --- | --- | --- | ---: |
| GND-SW | 8 / 9 | GND (fifth entry) | `0x127000` | Generator | 734 default, 2,341 at note 127 with maximum ramp |
| NFX-GN | 15 / 16 | NFX (new family after RAM) | `0x12E000` | Neighbour effect | 154 |
| NFX-SV | 9 / 10 | NFX | `0x128000` | Neighbour filter | 2,572–2,924 |
| NFX-4P | 14 / 15 | NFX | `0x12D000` | Neighbour filter | 2,931–3,469 |

All use the E12 layout: the E12 machines are removed and the banks sit in
their former sample area, in bus area 0 (see
[packing](12-packing-firmware.md#removing-e12-instead-memory-without-a-reservation)).
Sample memory and every other stock machine stay.

## GND-SW: PolyBLEP saw

A MIDI-tuned saw that reuses GND-SIN's controls, envelopes and state layout, so
GND-SIN's knobs keep their meaning.

| Knob | Meaning | Default |
| --- | --- | ---: |
| PTCH | MIDI note, A4 (69) = 440 Hz | 73 |
| DEC | GND-SIN's amplitude-decay coefficient | 96 |
| RAMP | GND-SIN's pitch-ramp amount | 0 |
| RDEC | GND-SIN's ramp-decay coefficient | 0 |

**Handler** (`control.s`), returns 5 words like GND-SIN:

| Word | Conversion |
| --- | --- |
| `+1` | `pitch[min(127, (raw + 64) >> 7)]`: nearest MIDI note, from a 128-entry table `round(440·2^((n−69)/12)·2^23/44100)` in flash |
| `+2` | `decay[raw >> 5]` from the stock table at `0x24BF94` |
| `+3` | `(raw·raw) >> 7` |
| `+4` | `decay[raw >> 5]` |

Rounding PTCH to the nearest note makes the OS's knob slews step through
semitones instead of detuning.

**DSP state** (GND-SIN's layout):

| Word | Contents | Reset by update (trig) |
| --- | --- | --- |
| `+1`–`+4` | Packet | — |
| `+5` | Untouched | — |
| `+6` | Pitch-ramp envelope | `0x7FFFF8` |
| `+7` | Phase, unsigned 23 bits | 0 |
| `+8` | Unused | 0 |
| `+9`, `+A` | Amplitude envelope, low and high words | 0, `0x7FFFF8` |

Init is `rts`.

**Render**:

1. **Block step**: add the ramp contribution (`ramp · ramp_env`, ×4 to convert
   GND-SIN's frequency units to 2^23-cycle phase units) to the base step.
   Clamp to `0x3FFFFF`, just below Nyquist, so an extreme ramp cannot fold the
   fundamental. Update the ramp envelope once per block.
2. **Oscillator**, 32 samples into `X:0x00–0x1F`:
   - The raw saw is `2p − Q` for phase `p` and `Q = 2^23`.
   - **PolyBLEP**: within one step `d` of the wrap, add `Q·(1 − p/d)²` on the
     left side or subtract `Q·(1 − (Q − p)/d)²` on the right.
   - A sample is outside both zones exactly when `|2p − Q| ≤ Q − 2d`. The
     kernel tests that with one `cmpm` against `K = Q − 2d` held in a register.
     Only edge samples call the correction subroutine (a 24-step `div`, then a
     square).
   - Store `saw >> 2`: quarter scale, like GND-SIN.
3. **Envelope**: GND-SIN's two-word amplitude envelope multiplies the scratch
   samples into the output bank.

The kit's `machine.s` is a cycle-optimized kernel generated from a simpler
reference. The two are bit-exact on output, oscillator scratch and state.
Measured cost: 734 cycles at the default note, 2,341 at note 127 with maximum
ramp (nearly every sample is an edge).

PolyBLEP reduces aliasing but does not remove it (about 15–18 dB less
foldback than a naive saw in tests).

## NFX-GN: neighbour gain

Scales the previous track's raw voice. It is the minimal neighbour effect,
written in full in [doc 11](11-writing-a-custom-machine.md).

| Knob | Meaning | Default |
| --- | --- | ---: |
| GAIN | 0 … about 1.98×, unity at 64 | 64 |

**Handler**: `packet[1] = (raw < 64 ? 0 : raw) << 9`, which is gain/2 in Q23
(`0x400000` = unity at knob 64, `0x7F0000` at 127). It returns 2 words.

**DSP**:

- Init and update are `rts`; there is no private state.
- Render:
  - on track 0, write 32 zeros;
  - otherwise read the other output bank (`Y:0x140 ^ 0x20`); for each sample
    `y = x·g·2`, stored with limiting.
- Bit-exact model: `y = clamp((x·g) >> 22, −2^23, 2^23 − 1)`.
- 23 words, 154 cycles per block.

It is deliberately unoptimized. The multiply can take the next sample's load as
a parallel move, which would roughly halve the loop.

## NFX-SV and NFX-4P: neighbour filters

A state-variable filter and a 4-pole ladder with the same eight knobs (FREQ,
RESO, MODE, ENVA, ATK, DEC, GAIN, VCA), a trig-driven AD envelope on the cutoff
and an envelope or gate VCA. The full description, packet and state layouts,
and hardware costs are in the examples' READMEs:
[NFX-SV](../tools/examples/nfx-sv/README.md), [NFX-4P](../tools/examples/nfx-4p/README.md)
and what they share, [common/nfx](../tools/examples/common/nfx/README.md).
What they show beyond NFX-GN:

- **A handler that calls another.** NFX-4P links `svf_control` (shared) and its
  own `ladder_control` into one blob; `ladder_control` calls `svf_control`, then
  replaces two words.
- **A table in internal X.** Both read a 1,025-word tanh table at `X:0x280`,
  uploaded as an X section (`mdkit.machine.add_x_table`, which keeps a single
  copy when machines share it). Internal reads cost no wait states.
- **Real handler work.** Table lookups with interpolation, a split
  coefficient (a 24-bit mantissa plus a scale flag in bit 23), and a gate
  length computed from the OS tempo word.
- **Block-rate control.** The envelope advances once per block by its exact
  32-sample step, the cutoff and the VCA gain ramp linearly across the block,
  and each machine has one filter loop per mode and envelope state, picked once
  per block.
- **Scheduled for the pipeline.** The filter loops were rescheduled with the
  interlock rules in [DSP programming](13-dsp-programming.md#pipeline-interlocks);
  they are the filters in its results table.

## Building your own

Start from the example whose shape is closest:

- **A generator** keeps phases and envelopes in its private state words and
  ignores the neighbour bank (GND-SW).
- **A neighbour effect** reads `Y:0x140 ^ 0x20` and must output silence on
  track 0 (NFX-GN; NFX-SV and NFX-4P for one with envelopes, state and tables).
- **An effect with memory** (a delay line) needs per-track storage outside the
  64-word state block. The E12 layout has a 16-track pool for it at
  `0x104000 + 0x2000·t` (`mdkit.machine.E12_POOL`). With E12 kept, reserve it
  from sample memory like the code region, at a lower address, and if the
  space needed exceeds what the RAM machines can spare, give them up (see
  [removing the RAM machines](12-packing-firmware.md#removing-the-ram-machines-to-gain-delay-memory)).
