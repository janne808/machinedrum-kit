# SysEx OS updates

An OS update `.syx` carries flash bytes from `0x4000` upwards: the compressed blob
chain, the version record and anything after it that the sender includes. It
does **not** carry the whole 8 MiB image, and the updater cannot write the boot
sector.

The stock `Elektron_SPS1-1UW_OS1.63.syx` carries 939,744 bytes for flash
`0x4000–0xE96DF`, in 14,684 data packets plus two terminator messages.

## Packet format

A data message:

```text
F0 00 20 3C 02 00 7E  CH CL  A5 A4 A3 A2 A1 A0  <payload>  F7
```

| Field | Meaning |
| --- | --- |
| `00 20 3C` | Elektron manufacturer ID |
| `02` | Product: Machinedrum |
| `00` | Reserved/device byte (always 0 in these files) |
| `7E` | Data packet |
| `CH CL` | Checksum, two nibbles (high, low) |
| `A5…A0` | Flash address, six nibbles, big-endian. The first packet is at `0x4000`. |
| payload | Up to 64 decoded bytes, 2+7+7 packed |

**2+7+7 packing.** Decoded bytes are taken in pairs as a big-endian 16-bit word
`w` and sent as three MIDI data bytes:

```text
(w >> 14) & 0x03,  (w >> 7) & 0x7F,  w & 0x7F
```

A full packet (64 bytes = 32 words) is 112 bytes including `F0`/`F7`. An odd
final length is padded with a zero low byte in the last word. The length message
then trims it.

**Checksum.** With the six address nibbles `n[0..5]` (`n[0]` = `A5`):

```text
checksum = (sum(decoded payload bytes)
            + n[1] + (n[2] << 4) + n[3] + ((n[4] & 0x0C) << 4)) & 0xFF
```

**Terminators.** After the data packets come exactly two messages:

```text
F0 00 20 3C 02 00 7E F7                      end of data
F0 00 20 3C 02 00 7F L5 L4 L3 L2 L1 L0 F7    total length
```

The six `L` nibbles give the total decoded payload length in bytes (counted from
`0x4000`). The byte after them selects the update path (`F7` here, see below).

Packets must be contiguous and in order. A decoder should reject:

- gaps, duplicates and reordering;
- bad checksums;
- data bytes with bit 7 set;
- short packets anywhere except the last;
- a wrong total length;
- trailing data.

Encoding the stock `.bin` with these rules reproduces the stock `.syx` byte for
byte.

## Exporting a custom image

The payload must run from `0x4000` to the **later** of:

- the end of the last compressed blob, and
- the last non-`0xFF` byte below `0x100000`.

Custom builds place descriptors, menus and ColdFire code in flash banks near
`0xFE000–0xFFFFF`, after the blob chain. An exporter that stopped at the last
blob would silently drop them, and the device would boot with dangling pointers
into erased flash. Include the `0xFF` gap in between: the updater programs
every word up to the highest address received.

Always verify an export by decoding it, applying it to the stock image outside
the transmitted range, and comparing with the intended `.bin`.

## What the boot-ROM updater does

The updater lives in the boot sector (`0x1236–0x172C` and helpers).

### Receive phase: nothing is written to flash

For each data packet:

1. Assemble the 24-bit address.
2. Stage at RAM `address + 0x1FC000`, so flash `0x4000` lands at RAM `0x200000`.
3. **Range check**: the staging address must be at most `0x2FFFFF`, so the flash
   address must be at most `0x103FFF`. There is no lower bound. Anything higher
   aborts with "UPGRADE FAILED".
4. Unpack 2+7+7 words into RAM, check the packet checksum (abort on mismatch).
5. Track the highest address received.

The `7F` length message must equal the number of bytes received. The whole
image is staged and checked before the first flash command, so an interrupted or
corrupt transfer changes nothing.

### Program phase

The erase, program and chip-ID routines are copied to internal SRAM
(`0x1000800–0x1000A00`) and run from there, because flash cannot be read while it
is being written.

For a **normal update** (the byte after the length nibbles is not `01`; stock
files send `F7`):

1. **Version check**: the staged image's version record must be greater than
   `"140\x1F"`, else "TOO OLD VERSION" and halt.
2. **Chip check**: if the flash ID is `0x2000D7` (ST29W800T, top-boot), print
   "E: ST29W800T" and halt.
3. **Erase** exactly 21 sectors: `0x4000, 0x6000, 0x8000, 0xA000, 0xC000, 0xE000,
   0x10000, 0x20000, …, 0xF0000`. Sector `0x0000–0x3FFF` is not in the list.
4. **Program** words from RAM `0x200000` up to the highest staged address into
   flash starting at `0x4000`.

Data sent below `0x4000` is staged in RAM `0x1FC000–0x1FFFFF` but never
programmed. **A normal update cannot modify the boot sector.**

The **extended-memory path** (byte `01`) needs a second flash chip (ID `0x2022DF`
or `0x2022FD`, else "NO EXTEND MEM"). It erases and programs from `0x100000`
upwards and also never touches `0x0000–0x3FFF`.

### Edge cases

- Addresses `0x100000–0x103FFF` pass the range check but are not erased by the
  normal path. In the board model that range is RAM, not flash. Keep payloads
  below `0x100000`.
- Unsent gaps inside the payload are programmed with whatever the staging RAM
  held. The length check counts bytes, not extent. Always send a contiguous
  payload.

## Recovery

The updater and bootloader live in the never-erased boot sector, and the boot
code checks the upgrade flag before it decompresses anything. A bad OS image
(one that hangs, fails a checksum, or crashes the OS) can therefore always be
replaced: enter the upgrade mode and send the stock `.syx` (**Static**: this
follows from the updater code).
