# GND-SW example

GND-SW, a MIDI-tuned PolyBLEP saw, as a **fifth GND machine** (firmware ID 8,
DSP type 9) on the stock OS 1.63 image. Its DSP and ColdFire code has run on
hardware in earlier custom builds. The build uses the
[E12 layout](../../../docs/12-packing-firmware.md#removing-e12-instead-memory-without-a-reservation):
the 16 E12 machines make room for the code, and sample memory, the ROM budget
and RAM recording and playback stay stock.

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
| `machine.s` | DSP2 code, 171 words at `P:0x127000`. A cycle-optimized kernel generated from a simpler reference, and bit-exact with it. |
| `control.s`, `pitch.inc` | ColdFire handler and its 128-entry MIDI phase-step table (`generate_pitch.py` rewrites it) |
| `build.py` | The build |

```sh
python3 build.py --firmware elektron_sps1-1uw_os1.63.bin \
    --asm /path/to/dsp56300-asm --output out/ [--version 163S]
```

What the build changes:

| Where | Change |
| --- | --- |
| MainOS GND family | List pointer `0x25239A`: stock list → `[GND---, SIN, NS, IM, SW, 0]` at flash `0xFF100` |
| MainOS ID table | ID 8 → the GND-SW descriptor at flash `0xFF000`; E12 IDs 48–63 → the empty machine |
| MainOS family table | The E12 record removed in place; P-I…RAM move up one slot (RAM is family 8) |
| DSP2 dispatch | Type 9 → GND-SW entries; E12 types 49–64 → the fallback |
| DSP2 upload | E12's 42 sample sections removed; P section at `0x127000`, in E12's former sample area (bus area 0) |
| Flash `0xFF200` | ColdFire handler, linked at `0x100FF200` |

Sample budgets and the ROM loaders are stock.

## Verified

In the emulator, booting the built image:

- **Budgets:** the startup budgets are stock, `0x140000`/`0x15F400` (48-ROM
  branch), and the four RAM slots are the stock ones, ending at `0x1FF9FE`.
- **E12 removed:** IDs 48–63 point at the empty machine, DSP types 49–64 at the
  fallback, and the families are GND, TRX, EFM, P-I, INP, MID, CTR, ROM, RAM.
- **RAM machines:** the RAM list and all RAM IDs are unchanged. **RAM-R1
  records**: it starts in its staging area, hands off to its slot at
  `0x18FC12`, and its recorded length grows. The front-panel browser shows RAM
  as family 8 with its eight machines and assigns them.
- **Menu and panel:** the GND menu has five entries. Selecting the fifth from
  the front panel assigns ID 8.
- **Registration:** the ID table, menu and dispatch cells read back as built.
- **Audio:** output and oscillator state equal the GND-SW integer model
  exactly, for 64 blocks at each of six settings, including notes 0/60/69/127,
  maximum decay and maximum ramp.

It has not been flashed to hardware in this configuration. The same GND-SW code
has run on hardware in earlier custom builds.
