# NFX-GN example

The neighbour gain machine from
[writing a custom machine](../../../docs/11-writing-a-custom-machine.md), built
into a complete OS 1.63 image. GAIN scales the previous track's raw voice from
0 to about 1.98×, with unity at 64. It appears in a new **NFX** family. The
build uses the
[E12 layout](../../../docs/12-packing-firmware.md#removing-e12-instead-memory-without-a-reservation):
the 16 E12 machines make room for the code, and every other stock family and
machine stays, RAM included, with stock sample memory.

| File | Contents |
| --- | --- |
| `machine.s` | DSP2 code (23 words at `P:0x12E000`) |
| `control.s` | ColdFire control handler |
| `build.py` | Assembles and links, applies every edit through `mdkit`, packs, checks, writes `.bin`, `.syx` and `build.json` |

```sh
python3 build.py --firmware elektron_sps1-1uw_os1.63.bin \
    --asm /path/to/dsp56300-asm --output out/ [--version 163G]
```

What the build changes:

| Where | Change |
| --- | --- |
| MainOS family table | E12 removed; copied to flash `0xFF000` with `NFX` appended as the tenth family (menu `[NFX-GN]` at `0xFF060`); the eight table references repointed |
| MainOS ID table | ID 15 → the NFX-GN descriptor (`0xFF080`); E12 IDs 48–63 → the empty machine |
| DSP2 dispatch | Type 16 → NFX-GN entries; E12 types 49–64 → the fallback |
| DSP2 upload | E12's 42 sample sections removed; P section at `0x12E000`, in E12's former sample area (bus area 0) |
| Flash `0xFF200` | ColdFire handler, linked at `0x100FF200` |

Sample budgets and the ROM loaders are stock.

## Verified

In the emulator, booting the built image:

- **Relocated family table:** all eight references point at it; the families
  are GND, TRX, EFM, P-I, INP, MID, CTR, ROM, RAM, NFX.
- **E12 removed:** IDs 48–63 point at the empty machine and DSP types 49–64 at
  the fallback; sample budgets are stock.
- **RAM machines:** still registered.
- **Registration:** the ID table, menu and dispatch cells read back as built.
- **Front panel:** the browser shows NFX with one entry, and ENTER assigns
  ID 15.
- **Audio:** with GND-SIN on track 1 and NFX-GN on track 2, every output
  sample equals `clamp((x·g) >> 22)` at GAIN 0, 1, 32, 64, 100 and 127 (six
  blocks each).
- **Track 1:** NFX-GN on the first track outputs silence.

It has not been flashed to hardware.
