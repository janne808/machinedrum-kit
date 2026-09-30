# GND-SW example

GND-SW, a MIDI-tuned PolyBLEP saw, as a **fifth GND machine** (firmware ID 8,
DSP type 9) on the stock OS 1.63 image. Its DSP and ColdFire code has run on
hardware in earlier custom builds. Every stock machine, including RAM recording
and playback, stays available.

| Knob | Meaning | Default |
| --- | --- | ---: |
| PTCH | MIDI note, A4 (69) = 440 Hz | 73 |
| DEC | GND-SIN's amplitude decay | 96 |
| RAMP | GND-SIN's pitch ramp amount | 0 |
| RDEC | GND-SIN's ramp decay | 0 |

The oscillator is a 23-bit phase saw with a two-sample quadratic PolyBLEP at
the wrap. A single `cmpm` per sample screens out samples with no edge. The saw
is shifted right by two and shaped by GND-SIN's amplitude envelope. The
envelope loop and state layout follow GND-SIN's so that its knobs keep their
meaning (see [custom machine reference](../../../docs/14-custom-machine-reference.md#gnd-sw-polyblep-saw)).

| File | Contents |
| --- | --- |
| `machine.s` | DSP2 code, 171 words at `P:0x1F3000`. A cycle-optimized kernel generated from a simpler reference, and bit-exact with it. |
| `control.s`, `pitch.inc` | ColdFire handler and its 128-entry MIDI phase-step table (`generate_pitch.py` rewrites it) |
| `../common/guards.s` | Sample-loader guards for the code-region reservation |
| `build.py` | The build |

```sh
python3 build.py --firmware elektron_sps1-1uw_os1.63.bin \
    --asm /path/to/dsp56300-asm --output out/ [--version 163S]
```

What the build changes:

| Where | Change |
| --- | --- |
| MainOS GND family | List pointer `0x25239A`: stock list → `[GND---, SIN, NS, IM, SW, 0]` at flash `0xFF100` |
| MainOS ID table | ID 8 → the GND-SW descriptor at flash `0xFF000` |
| MainOS sample budgets, two loader sites | Code region reserved from DSP2 `0x1F0000` up: 48-ROM budgets `0x120000`/`0x140000`, 32-ROM `0x0D0600`/`0x0E0000`, guards with ceiling `0x1F0000` |
| DSP2 dispatch | Type 9 → GND-SW entries |
| DSP2 upload | P section at `0x1F3000` |
| Flash `0xFF200` | ColdFire code (handler + guards), linked at `0x100FF200` |

## Verified

In the emulator, booting the built image:

- **Budgets:** the startup budgets read `0x120000`/`0x140000` (48-ROM branch).
  The four RAM slots are laid out by the stock partition formula; the last one
  ends at `0x1EFFFE`, below the code region.
- **RAM machines:** the RAM family and all RAM IDs are unchanged. **RAM-R1
  records**: it starts in its staging area, hands off to its slot at
  `0x18FC12`, and its recorded length grows.
- **Menu and panel:** the GND menu has five entries. Selecting the fifth from
  the front panel assigns ID 8.
- **Registration:** the ID table, menu and dispatch cells read back as built.
- **Audio:** output and oscillator state equal the GND-SW integer model
  exactly, for 64 blocks at each of six settings, including notes 0/60/69/127,
  maximum decay and maximum ramp.

It has not been flashed to hardware in this configuration. The same GND-SW code
has run on hardware in earlier custom builds.
