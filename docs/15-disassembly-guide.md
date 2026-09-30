# Disassembly guide

How to get correct listings of the ColdFire and DSP code from your own copy of
the firmware, and the mistakes that produced wrong conclusions in the past.

## 1. Extract

1. Check the image: 8,388,608 bytes, SHA-256 `68542e30…c44c8`.
2. Walk the blob chain from `0x4000` (see [firmware image](03-firmware-image.md#blob-records)).
   Verify each **compressed-byte** checksum.
3. Decompress MainOS, DSP2, DSP1, OS_A and OS_B (see [compression](04-compression.md)).
   Expected sizes: 404,766 / 750,369 / 56,469 / 524,288 / 524,288 bytes.
4. Parse both DSP streams into sections (see [DSP upload stream](03-firmware-image.md#dsp-upload-stream)).
   Save each section as its own file named by space and address, plus a manifest.
   Do **not** flatten them into one zero-filled image without keeping the
   section map (see pitfalls below).

## 2. ColdFire

MainOS is ColdFire ISA-A (MCF5206E). With GNU binutils:

```sh
m68k-linux-gnu-objdump -D -b binary -m m68k:isa-a --adjust-vma=0x200000 \
    mainos_decompressed.bin > mainos.asm
```

The boot sector disassembles the same way with VMA 0 (flash `0x0000–0x3FFF`).
MainOS also contains a relocated copy of boot routines around `0x249BF2`. This
copy is not the code that runs at reset.

In Ghidra, use the `68000`/ColdFire language (Coldfire:BE:32). Load MainOS at
`0x200000` and the boot sector at 0.

Things to know:

- A linear listing decodes data as instructions too: the descriptor table,
  lists, strings and the pitch/decay tables sit between code. Find data
  structures by their shape (86-byte records starting `00 20 …` with ASCII
  family names; tables of `0x0024…`/`0x0025…` pointers).
- **Absolute references** to a structure appear as 32-bit operands. To find
  every user of a table, search the image for its address as a big-endian
  word **and** for `address + 4` and other field offsets. Then confirm each hit
  is an instruction operand. This is how the eight family-table references
  were found.
- Code that reads flash does so through `0x1000xxxx` after the CS0 remap.
  Search for both forms when tracing flash readers.
- Control handlers are ordinary C-ABI functions: `4(sp)` packet, `8(sp)` raw
  words, `d0` return count. The per-machine handler address is the descriptor's
  first word.

## 3. DSP56300

DSP words are 24 bits; many instructions take a second word (an address or
immediate). Disassemblers:

- the dsp56300 emulator project's disassembler (C++), as used by the Gearmulator
  family of emulators;
- the disassembler in [mborgerson/dsp56300](https://github.com/mborgerson/dsp56300);
- a Ghidra DSP56K processor module (`DSP56K:LE:24`). Its decompiler output is
  unreliable for this code; use the listing.

Feed them **words**, not bytes: read the section files as 3-byte little-endian
words and give each word its address.

### Traverse; do not sweep

Disassemble by **recursive traversal from known roots**:

- the reset/interrupt vectors (`P:0x00–0xFF`, the entry `P:0x24`);
- the dispatcher loop `P:0x64`;
- **every pointer in the three dispatch tables** (`0x145AF5`, `0x145BB6`,
  `0x145C77`, 193 entries each: 151 distinct entry points).

Follow:

- direct jumps and calls;
- conditional fallthrough;
- `DO` loop exits (the loop-end word marks the last instruction; the label
  after it is the exit);
- `REP`.

Stop at `rts`/`rti`. This recovers about 11,400 machine instructions (11,700
for all of DSP2 including the scheduler and interrupts) with no
overlaps and no undecodable words. Machine code for each machine is the closure
from its three entries.

Linear sweeping is wrong here, because the uploaded sections include:

- 200k words of packed E12 samples;
- the dispatch tables;
- interpolation tables;
- P-I buffers;
- the machines' own lookup tables.

All of these decode as plausible nonsense.

### Pitfalls that caused wrong conclusions

1. **Instruction boundaries.** Starting one word off turns operands into
   opcodes. An older analysis read `0x000200` (an immediate) and three encoded
   `move` instructions at `P:0x101ABF–0x101AC4` as a "dispatch table of engine
   overlays". It was just code, entered mid-instruction.
2. **Zero-filled flat images.** Expanding the sections into a flat P image
   fills gaps with zeros, which decode as `nop`. Large "NOP regions" are unused
   address space, not overlay slots to be filled at runtime. Live memory in the
   board model fills unused P memory with RTS words instead, which shows that a
   flat file is not a live image. In every tested assignment and trig, DSP2
   made **no program-memory writes**: machines are resident, not loaded. The only
   self-modifying code found is DSP1's generated mixer.
3. **P/X/Y aliasing.** Above the alias base, `P:a`, `X:a` and `Y:a` are one word.
   A table uploaded in P is read by code as Y. Do not treat different views of
   the same address as different data.
4. **Peripheral names.** The DSP56303 has ESSI (not ESAI) and HI08. Use the
   DSP56303 user's manual register map. For example, `X:0xFFFFE6` is a DMA
   destination register, `X:0xFFFFEC` a DMA control register, and `X:0xFFFFF7`
   an address-attribute register. Wrong labels invented an "external SRAM
   FIFO" code-loading path that does not exist.
5. **Parallel moves** use the old register values. Accumulator arithmetic is
   56-bit with limiting on moves. Read listings with the family manual at hand,
   not as 24-bit integer code.

## 4. Confirm dynamically

Static listings give hypotheses; an instrumented emulator settles them. The
techniques used:

- **Breakpoints on the dispatch entries**, not on the call sites (an interrupt
  can run between the `jsr` and the target). Record registers on entry and on
  return to `P:0x8E` / `0x99` / `0xB5`.
- **Parameter probes**: set a knob by MIDI CC to its default, then to
  `default + 17`. Read the state block just before the DSP update call, and
  diff. Exclude words that also change between repeated identical trigs (phases,
  envelopes).
- **Write traces**: log every store a routine makes, to find its state layout.
  In the DSP emulator's JIT, a store of an unchanged value can be suppressed;
  a DMA write can appear with the PC of an unrelated instruction.
- **PC timelines**: record timestamps of chosen PCs on both DSPs and the
  ColdFire (for example DSP1 block start `P:0x4F` and each voice arrival
  `P:0x7E`, DSP2 render call/return `P:0xB4/0xB5`) to see who waits for whom.
- **Snapshots before and after** assignment and trig, to prove nothing else
  changed (this disproved the overlay theory: six machines from six families were
  activated, all 18 entry breakpoints hit, and zero program writes occurred).

The kit's monitor ([emulator harness](18-emulator.md)) provides all of these:
breakpoints, watchpoints, traces, `pclog`, MIDI and panel input, and LCD capture.
Its `disasm` gives live listings of both CPUs straight from emulated memory.
