# Firmware image

The OS 1.63 image is an 8 MiB flash dump (`0x800000` bytes). Only the first
megabyte is used; `0x100000–0x7FFFFF` is erased (`0xFF`) and is never touched by
an OS update.

## Flash layout

| Flash offset | Size | Contents |
| --- | --- | --- |
| `0x000000–0x003FFF` | 16 KiB | **Boot sector**: ColdFire vectors, bootloader, decompressor, DSP bootstrap stubs, **MIDI updater**. Never erased by the updater. |
| `0x004000` | 8 + 164,328 | Blob **MainOS** (ColdFire OS) |
| `0x02C1F0` | 8 + 587,978 | Blob **DSP2** (producer program and data) |
| `0x0BBAC2` | 8 + 39,310 | Blob **DSP1** (mixer program and data) |
| `0x0C5458` | 4 | **Version record**, ASCII `"163 "` |
| `0x0C545C` | 8 + 74,169 | Blob **OS_A** (factory data, full model) |
| `0x0D761D` | 8 + 73,915 | Blob **OS_B** (factory data, lite model) |
| `0x0E96E0–0x0FFFFF` | ~92 KiB | Erased (`0xFF`). Custom builds put their flash banks at the top of this gap. |
| `0x100000–0x7FFFFF` | 7 MiB | Erased |

These offsets are for the stock image. In a rebuilt image every record after a
recompressed one moves. Nothing stores absolute offsets for the records after
MainOS: the boot code walks the chain by sizes. Never copy stock offsets into a
rebuilt image.

The ColdFire reset vectors are at the start of the boot sector: SP = `0`,
PC = `0x0000000C`. The boot code sets up its own stack.

## Blob records

Every blob is a header followed by compressed data:

```text
+0   u32 BE  compressed_size   (bytes of compressed data that follow)
+4   u32 BE  checksum          (sum of the COMPRESSED bytes, modulo 2^32)
+8   ...     compressed data   (compressed_size bytes)
```

- The checksum covers the **compressed** bytes. Older notes say "sum of
  decompressed data", which is wrong. All five stock checksums match the
  compressed-byte sum.
- Records follow each other with **no padding**:
  `next = start + 8 + compressed_size`.
- The version record is 4 raw bytes between DSP1 and OS_A and is **not covered by
  any checksum**.

Chain walk (as the boot code and a correct extractor do it):

```python
p = 0x4000
mainos = header(p);  p += 8 + mainos.size
dsp2   = header(p);  p += 8 + dsp2.size
dsp1   = header(p);  p += 8 + dsp1.size
version = flash[p:p+4];  p += 4
os_a   = header(p);  p += 8 + os_a.size
os_b   = header(p);  p += 8 + os_b.size
```

The boot code reaches DSP2 as `*(u32*)0x4000 + 0x4008`: the MainOS size word
doubles as the pointer to the next record. It reaches OS_A as "DSP1 record end +
4", which depends only on the record's position, not its content.

Decompressed sizes: MainOS 404,766 bytes; DSP2 750,369; DSP1 56,469; OS_A and
OS_B 524,288 each. The codec is described in [compression](04-compression.md).

## Version record

Four ASCII bytes, stock `"163 "`. It is read in four places:

| Reader | Code | Use |
| --- | --- | --- |
| Boot ROM updater | `0x1688–0x16B4` | Reads the **incoming** image's record as a big-endian u32. If it is not greater than `0x3134301F` (`"140\x1F"`, signed compare), prints "TOO OLD VERSION" and halts before erasing anything. |
| Boot ROM splash | `0x207C–0x20C0` | Prints `K` then `c0 '.' c1 c2 c3` (stock: `K1.63`) |
| MainOS identity reply | `0x209638` | Universal Device Inquiry reply `F0 7E 00 06 02 00 20 3C 02 00 mm 00 c0 c1 c2 c3 F7`, each byte masked to 7 bits |
| MainOS info screen | `0x234E7E` | Model name plus `c0 '.' c1 c2 c3` |

To change it safely, use exactly 4 characters from digits, uppercase letters
and space. The first must not be a space, and the value must sort above
`"140\x1F"`. `"163N"` shows as `1.63N` (**Hardware**).

## OS_A and OS_B

Two alternative 512 KiB images decompressed to `0x100000`. The bootloader picks
OS_A when DSP2 is present (UW/full model) and OS_B otherwise. They are **data**,
not code: a machine and factory-kit database with a 10-byte header (magic
`0xD83FE3CF`), 0x460-byte records (machine-type records, then factory kits),
empty space, and a `PRESETS` footer. Custom machine work does not touch them;
packers copy their records verbatim.

## Boot sequence

Static reading of the boot code, consistent with the emulator boot:

```text
reset (PC = 0xC)
  init peripherals, probe DSPs
  if the upgrade flag is set:
      run the MIDI updater (see SysEx updates), then restart
  decompress OS_A (or OS_B if no DSP2) -> 0x100000   (skipped on warm start)
  decompress DSP2 blob -> scratch RAM 0x200000; upload to DSP2 over HI08
  decompress DSP1 blob -> scratch RAM 0x200000; upload to DSP1 over HI08
  decompress MainOS    -> 0x200000   (overwrites the scratch)
  clear 0x1000000..0x1001FFF, jump to 0x200000
```

Each decompression verifies its checksum. Failures print `E: DSP2 CHECKSUM`,
`E: DSP1 CHECKSUM`, `E: OS CHECKSUM` or `E: NO DSP` and halt. A warm-start marker
at RAM `0x17FFF8` (`'DAVE'` / `'AnDY'`) lets the loader skip redecompressing
OS_A.

Before the main upload, each DSP gets a small stage-1 stub through the DSP56303's
internal host bootstrap ROM: word count, load address `P:0x100`, then the words.
The stubs are stored in the boot sector. The stub then accepts the typed-section
stream below.

## DSP upload stream

A decompressed DSP blob is a stream of 24-bit words, **3 bytes each,
little-endian**. It is not an S-record format.

```text
word 0     3            preamble tag: entry point
word 1     entry        (0x000024 for both DSPs in OS 1.63)
word 2     4            preamble tag: configuration
word 3     config       configuration word
then repeated sections:
           tag          0 = P, 1 = X, 2 = Y
           address      start word address in that space
           count        number of words
           data[count]
terminator:
           3            end tag
           entry        must equal word 1
```

Parser (reference behaviour):

```python
def dsp_sections(data):
    w = [int.from_bytes(data[i:i+3], 'little') for i in range(0, len(data), 3)]
    assert w[0] == 3 and w[2] == 4
    entry, config, pos, sections = w[1], w[3], 4, []
    while True:
        tag = w[pos]; pos += 1
        if tag == 3:
            assert w[pos] == entry and pos + 1 == len(w)
            return entry, config, sections
        assert tag in (0, 1, 2)
        addr, count = w[pos], w[pos+1]; pos += 2
        sections.append(('PXY'[tag], addr, w[pos:pos+count]))
        pos += count
```

The stock DSP1 stream has 58 sections and DSP2 has 135. The first section loads
`P:0x000` (vectors). Sections can target external addresses in any space. Because
of the P/X/Y alias, a P section at `0x145AF5` fills the tables the dispatcher reads
through Y.

**Adding code to DSP2** is done by inserting extra sections **immediately before
the final `3, entry` terminator** (the last 6 bytes): `0, bank_address,
word_count, words…`. The loader uploads them like any other section before
starting DSP2. Changing an existing word (for example a dispatch pointer) means
locating the section that covers it and rewriting that word in place. An address
must be covered by exactly one section; see [packing firmware](12-packing-firmware.md).

Whole-stream sizes (stock, including tags and headers): DSP1 18,823 words, DSP2 250,123 words.
