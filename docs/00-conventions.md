# Conventions

## Addresses

- **ColdFire addresses are byte addresses.** Multibyte values in ColdFire memory
  are big-endian. MainOS addresses are given as CPU addresses (the OS runs at
  `0x200000`); the corresponding offset in the decompressed MainOS file is the
  address minus `0x200000`.
- **Flash file offsets and CPU addresses differ.** A flash byte at file offset
  `f` is read by the running OS at CPU address `0x10000000 + f` (the CS0 remap;
  see [memory maps](02-memory-maps.md#the-cs0-remap)). The text says "flash
  offset" or "CPU address" explicitly.
- **DSP addresses are 24-bit word addresses**, written with the space:
  `P:0x1B3000`, `X:0x148000`, `Y:0x800`. P, X and Y are separate spaces for
  internal memory. External SRAM is aliased so that the same physical word
  can appear in P, X and Y (see [memory maps](02-memory-maps.md#dsp-external-memory-aliasing)).
- DSP words in files (the upload stream and code packages) are stored as
  **three little-endian bytes** each.
- Offsets inside a DSP voice state block are written `+n` (hex), relative to the
  block base `S`.

## Numbers

- Hex is written `0x…` throughout. (The DSP assembler syntax in examples uses
  `$…`.)
- A 24-bit DSP word `w` read as a signed value is `w` if `w < 0x800000`, else
  `w − 0x1000000`.
- **Q23**: a signed fraction in 24 bits, value = signed(w) / 2^23, range
  [−1, 1 − 2^−23]. `0x7FFFFF` is the largest positive value; `0x800000` is −1.
  Some machines use `0x800000` (−1) as "exact unity" by negating in a
  single multiply (see [DSP programming](13-dsp-programming.md#exact-unity-with-negative-coefficients)).
- Unsigned phases are usually 23 or 24 bits: one cycle = 2^23 or 2^24.
- `Q = 2^23` in formulas.

## Tracks and machines

- Tracks are **zero-based** in all addresses and formulas (`t = 0..15`). The
  front panel labels them 1..16.
- **Firmware machine ID**: the number the OS stores in kits and uses to index its
  descriptor table (0..191).
- **DSP type**: the index into DSP2's dispatch tables. For every audio machine
  tested, DSP type = firmware ID + 1.
- The MIDI/SysEx "model" number used in the machine-assignment SysEx and the UW
  selector byte are a third representation. See the
  [machine catalogue](07-machine-catalogue.md#three-numbering-schemes).

## Certainty

Findings carry one of these levels where it matters:

| Label | Meaning |
| --- | --- |
| **Hardware** | Confirmed on a real Machinedrum running firmware built from these findings |
| **Verified** | Confirmed by execution in the emulator: breakpoints, memory reads, bit-exact output comparisons |
| **Static** | Read from the disassembly, not exercised |
| **Hypothesis** | A plausible interpretation that has not been checked |

Unlabelled statements in structure tables are **Verified**. Older community notes
contain claims that later execution disproved; they are listed in
[open questions and errata](17-open-questions.md#errata-to-older-notes).
