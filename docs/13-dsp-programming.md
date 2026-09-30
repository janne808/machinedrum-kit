# DSP programming notes

Practical DSP56300 knowledge from building the custom machines. The primary
references are the NXP *DSP56300 Family Manual* (instruction set, arithmetic,
addressing) and the *DSP56303 User's Manual* (memory, peripherals).

## Toolchain

- **Assembler**: [mborgerson/dsp56300](https://github.com/mborgerson/dsp56300)'s
  `dsp56300-asm` (tested commit `5b96b127`) is the reference. It is an absolute
  assembler with no linker and no macros. Assemble each machine at its bank
  address:

  ```text
  dsp56300-asm machine.s -I build -f lod -l program.lst
  ```

  The `lod` output is a56-style records: `P addr word` for code and data,
  `I addr name` for symbols. Take `machine_init`, `machine_update` and
  `machine_render` from the symbols.
- **Alternative**: Macroassembler AS (`cpu 56300`) has macros. Both assemblers
  produced identical words for the same source. AS's `p2bin` emits 4-byte
  big-endian words; convert them to 3-byte little-endian.
- **Validate the output before packing.** The tested assembler accepts
  overlapping `ORG`s without warning, and raw output drops addresses. So:
  - require one contiguous P segment starting at the bank;
  - require that it fits the bank's capacity;
  - reject any warning;
  - check that all three entries lie inside the segment.
- Sort symbols before writing manifests (the assembler emits them from a hash
  map).
- **DO loops**: put the end label on the instruction **after** the last loop
  instruction; both assemblers encode the preceding address.
- **Fixed-width forms**: `move #>$0,r4` forces the long form. Do not let an
  optimizer shorten instructions whose offsets you rely on.
- **ColdFire**: GNU binutils (`m68k-linux-gnu-as -m5206e`, `ld -Ttext=<alias
  address>`, `objcopy -O binary`, `nm`).

## Generated kernels

The larger machines keep a straightforward **reference kernel** and a
**generated optimized kernel**. The generator is a Python script that emits
unrolled or specialized assembly. It lets you:

- compute tables and constants in one place, shared with the test oracle;
- emit specialized loops per mode (for example one loop per filter mode, or per
  envelope state);
- keep the reference as the bit-exact specification: a differential test runs
  both kernels on random inputs and compares every output sample and state word.

## Arithmetic

- **Accumulators** are 56 bits: `A2:A1:A0` (8:24:24). A fractional multiply
  (`mpy`) produces `2·x·y` aligned so that `A1` is the Q23 result.
- **Moving an accumulator to memory or a register applies limiting**: if `A2`
  is not a sign extension of `A1`, the stored value saturates to
  `0x7FFFFF`/`0x800000`. This is usually what you want for audio output.
- **`move b,a` limits too; `tfr b,a` does not.** Use `tfr` to copy an
  accumulator exactly.
- **Masked integers**: after `and #mask,a`, take `a1` explicitly
  (`move a1,r0`). A full-accumulator move can saturate a negative intermediate
  even though the masked low word is in range. A ring-index bug came from this.
- **Shifts leave residue.** `asr` shifts bits into `A0`, and a following `asl`
  brings them back. If you need an integer, reload `A1` (or clear `A0`) before
  shifting left. `asr` of an odd value leaves a half-LSB in `A0`, which a later
  rounding instruction will see.
- **Rounding**: `mpyr`/`macr` round convergently (round half to even on the
  discarded bits). Model it exactly in reference code:

  ```python
  def rnd(acc48):             # convergent rounding to the high 24 bits
      hi, lo = acc48 >> 24, acc48 & 0xFFFFFF
      return hi + 1 if lo > 0x800000 or (lo == 0x800000 and hi & 1) else hi
  ```

- **`mpy x,#n,a`** multiplies by `2^-n` without a coefficient register.
- **Variable shifts**: `asl S1,S2,D` / `asr S1,S2,D` shift by a register count.
- **`clb`** counts leading bits, for normalization.
- **Conditional transfers** (`Tcc`, `IFcc` on ALU instructions) make branchless
  clamps, state switches and endpoint snaps.

### Exact unity with negative coefficients

A Q23 coefficient cannot represent +1.0, but `0x800000` is exactly −1.0. Several
machines store a gain or wet amount **negated** and use `mpy -x1,y0,a` or
`mac -…`. The coefficient range is then [−1, 0], and "unity" is exact without an
endpoint branch.

### Keeping sub-LSB precision

Slow envelopes and integrators stall if each step is below one LSB. Keep the low
24 bits of the accumulator (the fractional remainder) in a state word and add
them back on the next sample. Exponential envelopes, filter integrators and
one-pole smoothers all need this.

## Addressing and memory

- **Tables in your bank**: upload them as part of your P section and read them
  through the Y alias: `move y:(r4),y0` with `r4` pointing into the P bank.
  `movem` reads P directly but is slower. `move (r4)+n4` adds a table base held
  in `n4` without an ALU instruction.
- **Modulo addressing** (`Mn = size−1`) makes ring buffers free of wrap tests.
  **Restore `Mn` to `0xFFFFFF` before returning**; the next machine may not set it.
- **Parallel moves**: most ALU instructions can carry one X and one Y move, or a
  long move. Use them to load the next operands while computing. The moves use
  the old register values.
- **`X:0x00–0x1F`** is free scratch within one call.

## Speed techniques

These gave the large savings in the existing machines (typically 2–4×):

| Technique | Example |
| --- | --- |
| **Fast-path screens** | PolyBLEP needs a correction only near a discontinuity. One `cmpm` of the raw saw against a per-block threshold decides "no edge" in one instruction; the rare edge case jumps to a subroutine. |
| **Per-block work instead of per-sample** | Compute coefficients, reciprocals and envelope targets once per block. For example, a VCA as one linear gain ramp per block instead of a slew per sample: 182 instead of 510 cycles. |
| **Avoid `div`** | A 24-step `div` costs about 24 cycles plus setup. Precompute a per-block reciprocal on the DSP, or a coefficient on the ColdFire. |
| **Lookup + interpolation instead of rational functions** | A 2,048-interval table of a Padé saturator, interpolated linearly, replaced two divisions per sample. Index and fraction come from one magnitude via a mask and a parallel move. |
| **Specialized loops** | One loop per mode or envelope state, selected once per block (indirect jump), instead of per-sample branching. |
| **Branchless state machines** | `Tcc` switches envelope stage handlers at the peak or snap point without a branch. |
| **Two-pass render** | Pass 1 writes a per-sample modulation array (for example the cutoff) into the output bank; pass 2 reads each word and overwrites it with the output. No X memory needed. |
| **Cap the parameter range** | An oscillator capped at 11 kHz needs fewer PolyBLEP edge corrections per block at the top of the range, which bounds the worst case. |

**Guard the fast paths.** A fast path that silently stops being taken is
invisible to bit-exact tests: the output is identical, only slower. In one
case a control word changed from unsigned to signed. A `feedback ≥ 0` guard
then sent every setting to the slow reference loop, about 3.2× the cycles. The
emulator passed everything; hardware overran and went silent. Add
**cycle-ceiling tests** for the fast paths at every control setting.

## The stock sine table

DSP2 generates a 32,768-entry full-cycle sine at `0x148000–0x14FFFF` during boot,
with a recursive oscillator. It is read-only and shared (DSP1 has its own).

- Quadrants 1–3 are within about 6.5 LSB of an ideal sine.
- **Quadrant 4 is shifted by about 1.5 entries**, up to 2,412 LSB, and the last
  two entries are off. A nearest-entry lookup therefore has about −71 dBFS peak
  / −77 dB RMS error. Interpolation does not help; the error is in the table.
- Nearest-entry lookup costs about 6 instructions (top 15 phase bits → index).
  A polynomial sine costs about 20 but reaches −124 dB.

Use it where −71 dBFS is acceptable (an FM voice's operators can be); otherwise
compute sines or upload your own table.

## Budgeting

- **73,728 cycles** per block for DSP2's whole producer pass: 16 renders plus
  dispatch, updates, interrupts and DMA waits.
- An **idle** track costs 3,438 (the fallback renderer's padding). A machine
  cheaper than that makes an empty kit slot "free".
- Plan with the **worst block**, not the average: an envelope attack, all
  tracks triggered in the same block, maximum resonance, maximum pitch, all
  edges in one block.
- In the emulator, a heavy 16-voice kit around 62–63k cycles still meets the
  deadline, with about 11k cycles of slack left in DSP1. **Hardware is slower**:
  the emulator charges no external-memory wait states, and the machine code runs
  from external SRAM. How much slower has not been measured. Leave margin and
  test on hardware with the intended voice count.
- Measure a render's cost as the cycle count between the dispatcher's call
  (`P:0xB4`) and return (`P:0xB5`). Sum per track and compare the totals and
  the worst block with the budget.

| Machine | Measured (cycles/block) |
| --- | ---: |
| GND-SW | 734 default, 2,341 at note 127 with maximum ramp |
| NFX-GN | 154 |

As rough guidance from other custom machines built the same way:

| Kind of machine | Cycles/block |
| --- | ---: |
| Modulated delay (chorus/flanger) | ~1,500 |
| Delay with filtered feedback (fast path) | ~1,750 |
| Filter with an envelope | ~3,000 |
| Dual oscillator with PWM and LFO | ~3,900 |
| Oversampled ladder filter | ~4,200 |
