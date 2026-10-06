# Packing firmware

A custom image is made by editing the **decompressed** MainOS and DSP2 payloads,
recompressing those two, and reassembling the blob chain. Custom flash banks go
in the erased gap after the chain. Everything else stays byte-identical:

- the boot sector `0x0000–0x3FFF`;
- DSP1, OS_A and OS_B (copied as compressed records, only moved);
- all flash from `0x100000` up.

This is binary relocation and patching, not recompiling the OS.

## Pipeline

```text
1. read the stock image; check its SHA-256
2. walk the blob chain; verify all five compressed-byte checksums; decompress
3. parse the DSP2 stream into sections (keep byte offsets)
4. assemble every custom DSP program at its bank address
   assemble and link every ColdFire handler blob at its CPU alias address
5. MainOS edits (guarded, length-preserving): ID table, family table/menus,
   and, for builds that reserve sample memory, budgets and loader guards;
   RAM-machine removal only in builds that trade it for a delay pool
6. DSP2 edits: dispatch cells (in place), extra P sections before the terminator
7. build the flash banks: descriptors, menus, family table, handler blobs, tables
8. compress MainOS and DSP2; decompress again and compare byte for byte
9. reassemble: 0x4000: MainOS, DSP2, DSP1, version, OS_A, OS_B
   (DSP1/OS_A/OS_B records copied verbatim), then 0xFF up to the banks
10. checks (below); re-extract the new image and compare every payload
11. export the SysEx and check that it reconstructs the image
```

## Guarded edits

Every edit is **compare-before-write**: state the expected old bytes and fail if
they differ. This catches:

- the wrong source image;
- an edit applied twice;
- two features colliding on one word.

```python
def guard_replace(buf, offset, expected, replacement):
    assert len(expected) == len(replacement)
    if buf[offset:offset+len(expected)] != expected:
        raise ValueError(f'precondition mismatch at 0x{offset:x}')
    buf[offset:offset+len(expected)] = replacement
```

For a DSP2 dispatch cell, "expected" is the fallback value: the table's own
entry 0.

## ColdFire edits (MainOS)

All values are big-endian u32 unless shown as bytes. Addresses are CPU
addresses (file offset = address − `0x200000`). `A(x)` means the CS0 alias
`0x10000000 + x`.

### Machine registration

| Address | Old | New | Purpose |
| --- | --- | --- | --- |
| `0x252092 + 4·ID` | `0x24EF54` | `A(descriptor)` | ID table entry for each custom machine |
| `0x25239A` | `0x251E3E` | `A(GND menu)` | GND family list, with the new machine appended (GND-SW example) |

To add a family (the NFX-GN example adds NFX), copy the family table to a flash
bank with the new record appended and repoint the eight references (see
[catalogue](07-machine-catalogue.md#adding-a-family)):

| Address | Old | New |
| --- | --- | --- |
| `0x22C1A8`, `0x23065A` | `0x252396` | `A(new table)` |
| `0x22C210`, `0x231A60`, `0x231ABA`, `0x231B10`, `0x231F22`, `0x235368` | `0x25239A` | `A(new table) + 4` |

Once the table is relocated, later family changes (for example a longer menu
for the new family) edit the **copy** in flash, not MainOS.

### Reserving sample memory

Stock DSP2 sample memory (ROM slots, then RAM slots) starts at the DSP sample
base and ends at `base + total/2` words:

- **48-ROM branch**: `0x150000 + 0x15F400/2 = 0x1FFA00`;
- **32-ROM branch**: `0x180000 + 0xFFFF0/2 = 0x1FFFF8`.

Custom DSP code and delay rings in that region would be overwritten by samples
unless the region is **reserved**. To reserve everything from address `R` up:

1. On each branch, set `total = 2·(R − base)`, and lower the ROM budget by the
   same amount, which keeps the stock RAM minimum `total − ROM`. On the 48-ROM
   branch the ROM budget is a `moveq #n / swap` pair, so it must be a multiple
   of `0x10000`; round down.
2. Install the two **loader guards** with ceiling `R` (below).

The budgets are set at startup, per model branch:

| Address | Stock | Meaning |
| --- | --- | --- |
| `0x200376` | bytes `70 14` (`moveq #0x14` → `0x140000`) | 48-ROM ROM budget |
| `0x200382` | `0x15F400` | 48-ROM total budget |
| `0x2003AA` | `0x0F05F0` | 32-ROM ROM budget |
| `0x2003B6` | `0x0FFFF0` | 32-ROM total budget |
| `0x20D2A8` | 6 bytes | Loader path A → `jsr A(guard_loader_a)` |
| `0x20D7E2` | 8 bytes | Loader path B → `jsr A(guard_loader_b)`, `nop` |

Budgets are in 12-bit sample units (two per word). The ColdFire partitions RAM
slots from the end of the ROM samples up to `base + total/2`, so the RAM
machines keep working with proportionally less recording time.

The guards are small handlers in a flash bank. Each re-executes the
instructions it displaced, then rejects a sample if

```text
(length + 1)/2 + word_offset + sample_base (0x29F38E) + 0x100 > R
```

by jumping to the loader's own error path (`0x20D51E` / `0x20DA4A`), which clears
the slot. The guards also catch oversized banks already stored on the device.

| Reservation | `R` | 48-ROM ROM / total | 32-ROM ROM / total | Used by |
| --- | --- | --- | --- | --- |
| **Code region** | `0x1B0000` (`mdkit.machine.CODE_REGION`) | `0x0A0000` / `0x0C0000` | `0x050600` / `0x060000` | Custom DSP banks at `0x1B0000–0x1BFFFF`, all stock machines kept (the examples' earlier layout) |
| **Delay pool + code** | `0x190000` (`DELAY_POOL`) | `0x060000` / `0x080000` | `0x010600` / `0x020000` | Delay-line machines: 16 × `0x2000`-word per-track rings at `0x190000–0x1AFFFF`, then the code region. Usually combined with removing the RAM machines (below). About 8.9 s of ROM samples remain on 48-ROM; 32-ROM keeps very little. |

Both keep custom memory below `0x1C0000`, in DSP2 bus area 2. Area 3 above it
costs about 5 wait states per access and per code-fetch miss on hardware,
against about 1 in area 2
([measured](02-memory-maps.md#measured-wait-states-hardware-dsp2)). On
hardware, a memory-heavy reverb fitted 5 instances with its pool and code in
area 2, against 2 in area 3.

**The price is sample memory.** Sample memory is one contiguous range, so
reserving from `0x1B0000` gives up area 3 as well. The ROM budget and the RAM
slots both shrink:
- **ROM budget (48-ROM):** about 14.9 s (`0x0A0000` samples), against 29.7 s
  stock.
- **RAM slots:** the four slots share whatever the loaded ROM samples leave
  free below the reservation. With the emulator's ROM contents, each slot holds
  33,019 words, against 98,555 with the code region at `0x1F0000`.

Earlier builds reserved from `0x1F0000` (code only, `0x120000` / `0x140000`)
or from `0x1D0000` (pool and code, `0x0E0000` / `0x100000`). Those layouts
keep more sample memory, at area-3 speed.

`mdkit.machine.sample_reservation(R)` computes the values, and
`reserve_sample_memory()` applies them. With `R = 0x1B0000` the 48-ROM RAM
partition ends at `0x1AFFFE` in the emulator, and RAM-R1 still records into its
slot (**Verified** in the emulator; `R = 0x1F0000` behaved the same). Only the
48-ROM branch has been boot-tested.

### Removing the RAM machines to gain delay memory

Code banks and small tables fit in the code region, and all stock machines keep
working. Delay-line machines are different: a useful per-track delay ring is
thousands of words per track (16 × 8,192 words = 384 KiB for the pool above). A
build that wants that much memory can **give up RAM recording and playback in
exchange**:

| Edit | Old | New |
| --- | --- | --- |
| ID table `0x252092 + 4·ID`, ID ∈ {160–163, 165–168} | RAM-R1..R4, RAM-P1..P4 | `0x24EF54` (empty machine) |
| DSP2 dispatch cells for types 161–164, 166–169 | RAM entries | the fallback (entry 0) |
| Optionally, family 9 name `0x252396 + 9·8` | `RAM\0` | the new family's name, e.g. `NFX\0` |
| Optionally, family 9 list `0x25239A + 9·8` | `0x25206E` | `A(new menu)` |

Then reserve from the pool's base (`R = 0x190000`), as in the table above. With
the RAM machines gone, their share of sample memory can go to the delays as
well: set the budgets lower still, or reserve from a lower `R` (untested). Old kits and
SysEx assignments of RAM machines then produce silence instead of recording
over the delay rings.

Removing the RAM machines is only needed for this trade. A machine that needs
a code bank, or a small buffer that fits in the reserved region, should keep
them. `mdkit.machine.remove_ram_machines()` performs the ID and dispatch edits.

### Removing E12 instead: memory without a reservation

E12's sample data, `0x103DBA–0x135205` (201,804 words), is uploaded at boot
as 42 P sections: 21 samples, each followed by a `0x99`-word tail (see
[stock machines](10-stock-machines.md#algorithms-worth-knowing)). It sits
below all sample memory, so a build that gives up the 16 E12 machines can
put the delay pool and every code bank there and leave sample memory stock:

| Edit | Old | New |
| --- | --- | --- |
| ID table `0x252092 + 4·ID`, ID 48–63 | E12-BD…E12-BC | `0x24EF54` (empty machine) |
| DSP2 dispatch cells for types 49–64 | E12 entries | the fallback (entry 0) |
| DSP2 upload stream | E12's 42 sample sections | removed; match each by its exact (address, count) |
| Family 3 name / list (`0x252396 + 3·8`, `0x25239A + 3·8`) | `E12\0` / E12 list | another family, or a custom family |
| Sample budgets, loader guards | — | **none**: stock |

The DSP layout ([memory maps](02-memory-maps.md#custom-machine-regions)): the
pool at `0x104000–0x123FFF` and program banks at `0x124000 + 0x1000·k`,
k = 0…16, below the RAM recorders' staging areas at `0x135206`.

- **Match sections exactly.** The custom banks lie inside E12's range. A
  filter on "P sections inside `0x103DBA–0x135205`" also drops the custom
  programs appended to the stream. Remove only the 42 stock sections, by
  address and count, and assert the count and the word total.
- **Family slot.** The freed slot 3 can take any family. One build moved the
  RAM list (`0x25206E`) to slot 3 and used slot 9 for its custom family.
  This kept both RAM recording and the custom family in a stock-size table.
- **What stays.** E12's descriptors and coefficient tables
  (`0x103C7B–0x103DB9`) and its renderers stay uploaded and unused.
- **Who else reads the region.** Captured init/update/render traces show only
  the E12 renderers reading it. Number scans flag immediates in TRX-B2 and the
  ROM/RAM players that look like addresses there; the traces show they are not
  pointers. The traces cover default paths, not every parameter.
- **Old kits.** Kits or SysEx that assign E12 machines get silence.

**Verified** in the emulator (2026-10-05):
- the build boots with stock budgets;
- E12 assignments render silence;
- the RAM IDs and their dispatch match stock;
- RAM-R1 records into its stock slot;
- eight custom delay, filter, reverb and oscillator machines pass their tests
  at the new addresses.

**The kit's examples use this layout.** `mdkit.machine.remove_e12_machines()`
does all four edits; it removes the E12 family record in place, so P-I…RAM move
up one slot. The OS identifies families by ID and rebuilds its family index map
from the lists at boot. In the emulator, the browser lists and assigns P-I
(now family 3), ROM (7) and RAM (8) normally. Not yet run on hardware.

## DSP2 edits

### Dispatch cells

For each custom machine with DSP type `T = ID + 1`, overwrite in place:

```text
Y:0x145AF5 + T = init entry
Y:0x145BB6 + T = update entry
Y:0x145C77 + T = render entry
```

The tables are uploaded as P sections at those external addresses. Find the
single section covering each address, and rewrite the 3-byte word at
`section.byte_offset + 3·(address − section.address)`. Require that the old
value equals the table's entry 0.

To remove a machine (for example the RAM machines, above), set all three cells
back to entry 0's values.

### Program uploads

For each custom machine, insert before the final terminator:

```python
stream = stream[:-6] + words_le([0, bank, len(code)] + code) + stream[-6:]
```

Check that the entry and config words are unchanged afterwards.

Check that no stock section overlaps a custom bank or a reserved region. External P/X/Y
alias, so compare every section at or above the alias base regardless of its
space.

### Internal X data sections

Read-only tables for DSP2 internal X memory go into the same stream, as X
sections (tag 1). With `mdkit`:

```python
stream.append_section('X', 0x280, tanh_table)    # mdkit.image DspStream
```

Rules:
- **Stay inside the free gap** `X:0x257–0x6FF`. The stock stream's own internal
  X sections end at `0x256` (`0x202`, `0x203–0x222`, `0x223–0x242`,
  `0x243–0x24A`, `0x256`); `0x700` up is the master-return ring.
- **Upload identical sections once.** Several machines may declare the same
  table. Sections with different contents must not overlap.
- **Remember the X side when checking overlaps.** Below the alias base, only
  sections of the same space overlap, so compare internal X sections with the
  stock X sections, not with P.

The OS loads the whole stream at boot, before any machine runs. Nothing clears
the gap afterwards: a booted image keeps the table intact under full load.

### DSP program banks

Give each machine its own `0x1000`-word bank. In the
[E12 layout](#removing-e12-instead-memory-without-a-reservation) there is room
for 17 at `0x124000 + 0x1000·k` (`mdkit.machine.E12_CODE_REGION`); with a
reservation, 16 in the code region `0x1B0000–0x1BFFFF`. The examples use:

| Bank | Machine (ID) |
| --- | --- |
| `0x127000` | GND-SW (8) |
| `0x12E000` | NFX-GN (15) |

Code banks in `0x1B0000–0x1BAFFF` have run on hardware. So have banks in
`0x1F0000–0x1F9FFF`, in earlier builds (area 3, about five times the wait states
on cache misses). Machines keep lookup tables inside their bank and read them through
the Y alias.

### Optional DSP2 edits

- **Silent-renderer padding**: the fallback renderer's `DO #50` at `P:0x100093`
  (word `0x063280`) can be reduced, for example to 25 iterations (`0x061980`).
  Each idle track's render is then shorter, but its slot in the pass is still
  about one voice transfer (~3,000 cycles; see the
  [voice-link slot floor](08-dsp2-voice-abi.md#the-voice-link-slot-floor)). So
  the saving is only ~400 cycles per idle track, and the edit is off by default.

## Flash banks

Flash banks sit at the top of the erased gap and must stay below `0x100000`.
File offsets are shown; the OS sees `0x10000000 +`. One 4 KiB bank holds a lot:
a descriptor is 86 bytes, a menu entry 4 bytes, and a simple handler well under
1 KiB.

**GND-SW example**, bank `0xFF000–0xFFFFF`:

| Offset | Contents |
| --- | --- |
| `0xFF000` | GND-SW descriptor |
| `0xFF100` | GND menu: the 4 stock entries, GND-SW, 0 |
| `0xFF200` | ColdFire code: `saw_control` + pitch table, linked at `0x100FF200` |

**NFX-GN example**, bank `0xFF000–0xFFFFF`:

| Offset | Contents |
| --- | --- |
| `0xFF000` | Relocated family table: the 10 stock families, NFX, terminator (96 bytes) |
| `0xFF060` | NFX menu: NFX-GN, 0 |
| `0xFF080` | NFX-GN descriptor |
| `0xFF200` | ColdFire code: `gain_control`, linked at `0x100FF200` |

Several machines can share one ColdFire code blob, linked once. Blobs linked
separately can call each other's helpers through `--defsym name=address` at
link time; every such address must be a CS0 alias address.

More banks can go below the first, provided that the blob chain ends before
the lowest one. The packer must check that the chain end, plus a margin for
recompression growth, stays below the lowest bank.

## Reassembly

```python
chain = b''
for name in ('MainOS', 'DSP2', 'DSP1', 'version', 'OS_A', 'OS_B'):
    chain += new_record(name) if edited(name) else stock_record_bytes(name)
image = bytearray(stock)
image[0x4000:0x100000] = b'\xff' * (0x100000 - 0x4000)
image[0x4000:0x4000 + len(chain)] = chain
for offset, data in banks:
    image[offset:offset + len(data)] = data
```

The version record goes between DSP1 and OS_A. Write the stock `"163 "` or a
custom one (see [firmware image](03-firmware-image.md#version-record)).

## Checks

Before writing the output:

1. **Round trips.** Every recompressed payload decompresses to exactly the
   edited payload. Re-extract the finished image: all five checksums pass and
   every payload matches.
2. **Untouched regions.** `image[:0x4000] == stock[:0x4000]` and
   `image[0x100000:] == stock[0x100000:]`.
3. **No overlap.** The chain ends below the lowest flash bank; the gap was `0xFF`
   in the stock image; banks do not overlap each other.
4. **CS0 alias.** No 32-bit word in the flash banks, at any even offset, lies in
   `[lowest_bank, 0x100000)`, and no MainOS edit's new value does. Those would
   be low-window pointers, which hang real hardware.
5. **DSP.** No custom bank overlaps an uploaded section or a reserved region. Entry and
   config words are unchanged. Every dispatch edit found its expected fallback.
   Internal X sections lie inside `X:0x257–0x6FF` and do not overlap each other
   unless identical.
6. **Version** reads back as intended.
7. **SysEx.** Encode, decode, apply to the stock image; the result equals the
   new image.

A boot test then checks the result in live memory (see
[emulation and verification](16-emulation-and-verification.md)).
