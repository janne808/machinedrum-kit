# NFX-GN example

The neighbour gain machine from
[writing a custom machine](../../../docs/11-writing-a-custom-machine.md), built
into a complete OS 1.63 image. GAIN scales the previous track's raw voice from
0 to about 1.98×, with unity at 64. It appears in a new **NFX** family. All stock
families and machines stay, RAM included.

| File | Contents |
| --- | --- |
| `machine.s` | DSP2 code (23 words at `P:0x1BA000`) |
| `control.s` | ColdFire control handler |
| `../common/guards.s` | Sample-loader guards for the code-region reservation |
| `build.py` | Assembles and links, applies every edit through `mdkit`, packs, checks, writes `.bin`, `.syx` and `build.json` |

```sh
python3 build.py --firmware elektron_sps1-1uw_os1.63.bin \
    --asm /path/to/dsp56300-asm --output out/ [--version 163G]
```

What the build changes:

| Where | Change |
| --- | --- |
| MainOS family table | Copied to flash `0xFF000` with an eleventh family `NFX` (menu `[NFX-GN]` at `0xFF060`); the eight table references repointed |
| MainOS ID table | ID 15 → the NFX-GN descriptor (`0xFF080`) |
| MainOS sample budgets, two loader sites | Code region reserved from DSP2 `0x1B0000` up (bus area 2) |
| DSP2 dispatch | Type 16 → NFX-GN entries |
| DSP2 upload | P section at `0x1BA000` |
| Flash `0xFF200` | ColdFire code (handler + guards), linked at `0x100FF200` |

## Verified

In the emulator, booting the built image:

- **Relocated family table:** all eight references point at it; the families
  are GND…RAM plus NFX.
- **RAM machines:** still registered.
- **Registration:** the ID table, menu and dispatch cells read back as built.
- **Front panel:** the browser shows NFX with one entry, and ENTER assigns
  ID 15.
- **Audio:** with GND-SIN on track 1 and NFX-GN on track 2, every output
  sample equals `clamp((x·g) >> 22)` at GAIN 0, 1, 32, 64, 100 and 127 (six
  blocks each).
- **Track 1:** NFX-GN on the first track outputs silence.

It has not been flashed to hardware.
