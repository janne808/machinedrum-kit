# Memory maps

## ColdFire

| CPU address | Contents | Notes |
| --- | --- | --- |
| `0x00000000–0x000FFFFF` | Boot flash, **only until the OS remaps CS0** | The boot ROM and bootloader run here. After the remap nothing decodes this range on hardware. |
| `0x00100000–0x0017FFFF` | OS_A (or OS_B) factory data, decompressed at boot | 512 KiB preset and machine database. It is data, not code (see [firmware image](03-firmware-image.md#os_a-and-os_b)). |
| `0x00200000–0x00262D1D` | MainOS, decompressed (404,766 bytes) | Code, then initialized data. Runtime globals and BSS follow directly, so **MainOS cannot grow**. |
| `0x00262D1E–0x002FFFFF` | MainOS globals, BSS, task stacks | For example the descriptor-handler slots at `0x29F27C`, sample-bank globals around `0x29E9F0`–`0x29F6DE`, menu state at `0x28C2D8` and `0x28D838`. |
| `0x00300000–0x0030FFFF` | SIM peripherals (MBAR = `0x300000`) | Chip selects, timers, UARTs, interrupt controller. |
| `0x00500000–0x00500007` | DSP1 HI08 host registers | 8 bytes; no window into DSP memory. |
| `0x00600000–0x00600007` | DSP2 HI08 host registers | 8 bytes. |
| `0x00700000–0x007FFFFF` | Kit, pattern and song working storage | For example the current kit at `0x70000A` and 64 stored kits from `0x7008CA` (see [ColdFire OS](06-coldfire-os.md#kits)). In the board model this is an alias of the RAM at `0x100000`. |
| `0x01000000–0x0100FFFF` | ColdFire internal SRAM | Vector base, interrupt code and live state; for example the tempo at `0x100150C`. |
| `0x10000000–0x107FFFFF` | **Boot flash through chip select 0, after the remap** | The only address range at which flash is readable once the OS runs. |

### The CS0 remap

During initialization MainOS reprograms chip select 0 (code at `0x2002C8`):

```text
CSAR0 (MBAR+0x64) = 0x1000       -> base 0x10000000
CSMR0 (MBAR+0x68) = 0x007F0000   -> 8 MiB window
```

From then on, **flash is only readable at `0x10000000 + file offset`**. The stock
OS reads the version record via `0x10004000`, for example.

**Hardware.** Every pointer that the OS dereferences into flash, and every link
address of code executed from flash, must use this alias. A custom descriptor
placed at file offset `0xFF000` must be referenced as `0x100FF000`, never as
`0xFF000`. Images that used low pointers booted in the emulator but hung on a
real Machinedrum at boot, and again when entering kit edit. The emulator maps
both windows permanently and ignores CSAR0.

A packer should reject any word in the custom flash banks, and any MainOS edit,
that points into `0x00000000–0x000FFFFF`
(see [packing firmware](12-packing-firmware.md#checks)).

## DSP external memory aliasing

On DSP2, external SRAM is wired to the P, X and Y buses at the same addresses:
`P:a`, `X:a` and `Y:a` are the same physical word for external addresses. In
the board model this aliasing applies from `0x20000` upwards. Consequences:

- A table uploaded as a P section can be read by DSP code through `Y:` or `X:`.
  Custom machines keep lookup tables in their own P bank and read them through
  the Y alias.
- The dispatch tables at `0x145AF5…` are uploaded as P data and read by the
  dispatcher as Y. Older notes read the P view and the Y view as different
  tables; they are the same table.
- When checking overlaps, treat P/X/Y as one physical space above the alias
  base, not three pools.
- Writing through X or Y changes "program memory", so the emulator's JIT must be
  invalidated when a live patch does this.

Internal memory (low addresses) is separate per space: `P:0x100`, `X:0x100` and
`Y:0x100` are three different words.

The internal/external boundary on the real chip depends on the DSP56303 memory
configuration (OMR memory-switch mode, instruction cache). It has not been read
from hardware; see [open questions](17-open-questions.md).

## DSP2

### Internal and low memory

| Address | Use |
| --- | --- |
| `P:0x000–0x0FF` | Vectors, dispatcher loop (`P:0x64`), host ISRs (`P:0xE8` receive, `P:0xF4` X readback) |
| `P:0x13D–0x16C…` | Resident ROM/RAM sample-player code (UW machines) |
| `X:0x000–0x01F` | Shared scratch used by stock renderers within one call (for example GND-SIN's oscillator block) |
| `X:0x100–0x1FF` | ADC input ring, filled by DMA1 (INP and RAM machines) |
| `X:0x243` | Master-return ring pointer |
| `X:0x700–0x7FF` | Stereo master return from DSP1 (DMA2) |
| `Y:0x100–0x11F`, `Y:0x120–0x13F` | The two alternating 32-word voice output banks |
| `Y:0x140` | Base of the output bank for the current voice |
| `Y:0x141` | Current voice state pointer |
| `Y:0x142` | Current track index |
| `Y:0x153 + t` | Active DSP type of track `t` |
| `Y:0x800 + 0x40·t` | **Voice state block `S`**, 64 words per track (`Y:0x800–0xBFF`) |

### External memory

| Address (P/X/Y alias) | Contents |
| --- | --- |
| `0x100000–0x103C7A` | Resident machine code (GND, TRX, EFM, E12 wrappers, INP, RAM recorder…) and helpers |
| `0x10008E` / `0x10008F` | The shared empty init/update entry and the silent renderer |
| `0x103C7B–0x103D7A` | E12 coefficient tables |
| `0x103D7B–0x103DB9` | E12 sample descriptors: 21 × {start, length, 0} |
| `0x103DBA–0x135205` | E12 sample data, packed two 12-bit samples per word (201,804 words) |
| `0x135206–0x135405` | RAM-recorder staging areas: four, `0x80` words apart, bases `0x135206/286/306/386` |
| `0x135600–0x13B5FF` | P-I resonator delay buffers (cleared at boot) |
| `0x142100–0x145ABx` | P-I machine code (entry points `0x142100`…`0x145ABB`) |
| `0x145AF5` / `0x145BB6` / `0x145C77` | **Init, update and render dispatch tables**, 193 entries each |
| `0x147200`, `0x147400` | Interpolation tables |
| `0x147E00–0x147F07` | Sample metadata: four-word records per ROM/RAM slot, RAM owner words at `0x147F04…` |
| `0x147FFF` | Boot marker (`0x64` cold, `0x65` warm) |
| `0x148000–0x14FFFF` | **32,768-entry sine table**, generated at boot (see [DSP programming](13-dsp-programming.md#the-stock-sine-table)) |
| `0x150000–…` | User (UW) sample memory, ROM slots then RAM slots. The base is `0x150000` in the 48-ROM configuration and `0x180000` in the 32-ROM one. |

### Custom-machine regions

Stock sample memory extends to about `0x1FFA00` (48-ROM) or `0x1FFFF8` (32-ROM),
so custom memory must be **reserved** from it first (see
[packing firmware](12-packing-firmware.md#reserving-sample-memory)). Two
reservations are in use:

| Region | Reserved by | Use |
| --- | --- | --- |
| `0x1F0000–0x1FFFFF` | **Code region**: lower sample budgets + loader guards with ceiling `0x1F0000`. All stock machines stay, and RAM recording keeps working with 64K words less memory. | Custom DSP program banks, `0x1000` words each |
| `0x1D0000–0x1EFFFF` | **Delay pool** (optional): reserve from `0x1D0000`. Delay-line machines need this much; builds that use it usually give up the RAM machines to free the space. | 16 × `0x2000`-word per-track delay rings (`0x1D0000 + 0x2000·t`) |

Code banks in `0x1F0000–0x1F9FFF` have run on hardware in earlier custom builds.
The kit's examples use `0x1F3000` (GND-SW) and `0x1FA000` (NFX-GN).

The emulator also treats `P:0x400–0x4FF` as unused (filled with RTS words). Early
prototypes placed code there. It is not uploaded by the stock firmware, but its
availability on hardware is unproven, so current builds do not use it.

## DSP1

| Address | Use |
| --- | --- |
| `P:0x3C–0x4F` | Block scheduler: waits for ADC DMA position `0x13F` / `0x17F` |
| `P:0x9AA` | Host-command receive ISR (destination and count, then DMA5) |
| `P:0x9DE–…` | Mixer loop; `P:0x9E2…` is **generated code**, rebuilt each block |
| `X:0x100–0x17F` | ADC input halves |
| `X:0x180–0x1BF` | Dry stereo bus |
| `X:0x1C0–0x1FF` | Reverb send bus |
| `X:0x200–…` / `…–0x3FF` | Processed track blocks: MAIN-routed growing up from `0x200`, mono-routed growing down from `0x3E0` |
| `X:0x400–0x4BF`, `X:0x4C0–0x57F` | DAC double buffer, 32 frames × 6 channels per half |
| `X:0x600–0x63F` | Delay send bus / delay return |
| `X:0x640`, `X:0x641` | Current DAC and ADC half pointers |
| `X:0x688–0x6C7` | Master block copy sent back to DSP2 |
| `X:0x6C8–0x6D7` | Per-block snapshot of the 16 output routes |
| `Y:0x100 + 5·t` | Track route, volume, pan, reverb send, delay send |
| `Y:0x150–0x165` | Master delay parameters and state |
| `Y:0x170–0x1BD` | Master EQ, dynamics and reverb parameters and state |
| `Y:0x1C4`, `Y:0x1C5`, `Y:0x1C7` | Next voice-block address, current track, track-state pointer |
| `Y:0x200 + 0x40·t` | Per-track effect state (AM, EQ, filter, SRR, distortion) |
| `Y:0x600–0x7FF` | Voice blocks received from DSP2 (DMA4 ring) |
| External `0x10011A–0x117C15` (+`0x17AFC`) | Master delay lines (two) |
| External `0x1439xx`, `0x143D00` | Reverb network state, dynamics look-ahead ring |

Details are in [DSP1 audio path](09-dsp1-audio-path.md).
