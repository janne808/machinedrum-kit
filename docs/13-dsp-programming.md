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
- **A short immediate into a data ALU register is a fraction.** `move #16,a`
  loads the 8-bit value into the most significant bits: `a1 = $100000`, not 16.
  The same goes for `x0`, `x1`, `y0` and `y1`. Only address registers take a
  short immediate as an integer (`move #16,r4`). Write integers into data ALU
  registers in the long form, `move #>16,a`. The ALU-instruction forms behave
  differently: `cmp #16,a`, `add #xx,a` and `sub #xx,a` use their 6-bit
  immediate as an integer, as verified kernels rely on. When in doubt, use
  `#>` everywhere. In one machine, `move #16,a` for "16 − k ticks" made a DO
  loop count about a million, and the kernel runner timed out inside the
  loop. If a kernel hangs in a DO loop, check how its count was loaded first.
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
- **Hot tables belong in internal memory.** Every external data access pays
  bus wait states on hardware: about 6 cycles per access in area 3
  (`0x1C0000+`) and about 2 in areas 0–2, where the code region and delay pool sit
  ([measured](02-memory-maps.md#measured-wait-states-hardware-dsp2)). Code
  fetches mostly hit the instruction cache; a miss costs the same. See [internal-memory tables](#internal-memory-tables).

## Speed techniques

These gave the large savings in the existing machines (typically 2–4×):

| Technique | Example |
| --- | --- |
| **Fast-path screens** | PolyBLEP needs a correction only near a discontinuity. One `cmpm` of the raw saw against a per-block threshold decides "no edge" in one instruction; the rare edge case jumps to a subroutine. |
| **Per-block work instead of per-sample** | Compute coefficients, reciprocals and envelope targets once per block. For example, a VCA as one linear gain ramp per block instead of a slew per sample: 182 instead of 510 cycles. |
| **Avoid `div`** | A 24-step `div` costs about 24 cycles plus setup. Precompute a per-block reciprocal on the DSP, or a coefficient on the ColdFire. |
| **Lookup + interpolation instead of rational functions** | A 2,048-interval table of a Padé saturator, interpolated linearly, replaced two divisions per sample. Index and fraction come from one magnitude via a mask and a parallel move. Keep per-sample tables in internal X ([below](#internal-memory-tables)). |
| **Block-rate envelopes** | Advance an exponential AD envelope once per block by its exact 32-sample step `e = 1 − (1 − k)^32`, computed as five rounds of `e ← 2e − e²` on the accumulator, and ramp the parameters it drives linearly across the block. This replaces about 30 cycles per sample of envelope and parameter passes with about 4. |
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

## Pipeline interlocks

The DSP56300 pipeline stalls when an instruction needs a result that the
previous one is still producing. **The emulator charges none of these
interlocks**: its cycle table assumes no dependence on earlier instructions. On
hardware they are real cycles, invisible to bit-exact tests and to the
emulator's budget. In tight per-sample loops they added 10–25 % to the
measured machines.

### The rules

From the DSP56300 Family Manual (Appendix A, pipeline interlocks):

| Interlock | Cost | When |
| --- | ---: | --- |
| Arithmetic stall | +1 | The move part reads an accumulator (or a part of one) that the **previous** instruction's ALU part wrote |
| Transfer stall | +1 | The move part reads an accumulator that the previous instruction's **move** part wrote |
| AGU interlock | +3 / +2 / +1 | An address uses `Rn` within one / two / three instruction cycles of a move that wrote `Rn`, `Nn` or `Mn` |
| `Tcc` then an R source | +1 | The instruction after a `Tcc` reads an address register |
| `Jcc` not taken | +1 | A conditional jump falls through |
| One-instruction `do` loop | +1 | Once per loop, not per pass |

Typical offenders:

```asm
        move    x:(r0)+,a                     ; load
        move    a,y:(r1)+                     ; +1 transfer stall: A was just loaded

        add     x0,a
        move    a,y:(r1)+                     ; +1 arithmetic stall

        move    #table,r4
        move    x:(r4),x0                     ; +3 AGU interlock
```

### Estimating them

Apply the rules statically to the executed code, weighted by each instruction's
execution count from a per-instruction profile of a typical and a worst block.
The predecessor of a loop's first instruction is the `do` on entry and the
loop's last instruction on every other pass. One script of about 150 lines does
this. Rank the stalls by instruction and by label; they cluster in the inner
loops.

### Fixes that kept the arithmetic identical

All of these were bit-exact with the unchanged models:

- **Alternate the accumulators.** Copies and loads alternate `a` and `b`, so a
  value is never stored right after it was loaded or computed.
- **Interleave two independent chains.** Process two samples (or two taps,
  two filter stages) at once, one in `a` and one in `b`. Each result then has
  an instruction's distance before it is read.
- **Fold adds into `mac`.** Load the addend into the accumulator first, then
  `mac`, instead of `mpy` followed by an `add`: one instruction fewer, one
  dependence fewer.
- **Shifted adds as multiplies.** `mpy ±y1,#n,a` and `mac ±y1,#n,a` multiply
  by 2^−n: a tap / 4 is `mac y1,#2,a`, with no separate shift.
- **Fill gaps with independent loads.** Where a result must wait, put the next
  sample's loads or an address update in the gap, using a free parallel move.
- **Set address registers early.** Write `Rn`, `Nn` and `Mn` three instructions
  before they are used, or use them through a different register.
- **Unroll only to interleave.** `do` loops have no per-pass overhead, so
  unrolling gains nothing by itself and costs instruction-cache space (1K
  words shared by all 16 tracks).

Check the scheduled code with the same differential tests and cycle ceilings
as any optimization. Scheduling can cost a few emulated cycles (a filler)
while saving more on hardware. Judge it by the estimate plus the emulated
count, not by the emulated count alone.

### Results

Per instance and block, before → after scheduling:

| Machine | Emulated cycles | Estimated interlocks | Hardware |
| --- | --- | --- | --- |
| Two-track reverb (per pair) | 4,857 → 4,469 | 1,137 → 380 | Generator + 7 pairs + load meter: first underrun 30 → **42** steps (512 cycles each): at least ~880 cycles freed per pair, against ~990–1,090 predicted. Both tracks of each pair now sit at the slot floor, so the gain is a lower bound. The emulator's threshold moved only 53 → 51. |
| 4-pole ladder filter | unchanged | ~950 → ~500 | Generator + 14 instances with envelope cutoff and envelope VCA: ~700 above the slot floor per instance |
| State-variable filter | +65 | ~755 → ~500 | Same kit: ~180 above the floor per instance |
| Dual oscillator | unchanged | 385 → 161 (worst block) | 15 instances: at the floor, with the same headroom whether the oscillators are plain static saws or PWM with slide |

**How accurate.** The estimate predicted the reverb's improvement within
10–20 %. For machines that remain above the floor, the hidden cost left after
scheduling (hardware minus emulated) matched the estimate within ~10 % in one
kit and was ~1.5× lower than the estimate in another. Use the estimate to rank
hot spots and to predict improvements. Measure the absolute cost with a load
meter.

**The slot floor caps the gain.** A machine already under ~3,100 cycles per
track on hardware gains nothing from scheduling (see [budgeting](#budgeting)).
Schedule the machines that are above the floor, and the hot loops of
multi-track machines.

## Internal-memory tables

**Why.** Hardware measurements showed that external data reads are the main
cost the emulator does not see:
- A 2×-oversampled ladder filter read 128 words per block from a table in its
  P bank, through the external alias. 8 enveloped instances fitted on
  hardware, against 15 in the emulator.
- The same machine with the table moved into internal X fitted 13.
- With a block-rate envelope as well, it fitted 15: a full kit after one
  oscillator.
- A dual oscillator with no external data reads matched the emulator: 16
  instances.

**Where.** `X:0x257–0x6FF` is free internal X memory, 1,193 words (see
[memory maps](02-memory-maps.md#internal-memory)). Upload tables there as X
sections of the DSP2 stream (see [packing](12-packing-firmware.md#internal-x-data-sections)).
Machines read them with `x:(r)`, so the lookup can run in parallel with Y
moves. Never write them at run time.

**The shared tanh table.** `X:0x280–0x680` holds 1,025 words:

```python
T = [round(2**23 * math.tanh(8 * i / 1024)) for i in range(1025)]   # tanh(8u), u = 0..1
```

- **Accuracy:** with linear interpolation, the worst error against `tanh` over
  0 ≤ x < 8 is 55 LSB (−103.7 dBFS).
- **Range:** `tanh(8)` = 1 − 2·10⁻⁷, so clamping at x = 8 costs nothing.
- **Odd symmetry:** only magnitudes are stored, and the lookup applies the sign
  afterwards.
- **Sharing:** every machine that wants tanh, or a scaled tanh, uses the same
  words. The packer uploads identical sections once.

**Lookup.** Bring the argument to `x/8` in accumulator A (1.0 = `2^47`). Then:

```asm
; In: A = x/8 (48-bit, any sign). N3 = $1FFF, N4 = $280.
; Out: A = +-tanh(x) (Q23, floored), A0 = 0. Uses B, X0, Y0, Y1, R4, N5.
        abs a           a2,n5         ; |x/8|; capture the sign
        move a,y1                     ; limited: |x| >= 8 reads as $7FFFFF (last entry)
        tfr y1,b        n3,x0
        mpy y1,#13,a                  ; index = top 10 bits (A1)
        and x0,b        a1,r4
        asl #10,b,b                   ; fraction = low 13 bits, as Q23
        move (r4)+n4
        move x:(r4)+,x0 b,y0          ; T[i]
        move x:(r4),y1                ; T[i+1]
        mpy y0,y1,a
        mac -y0,x0,a    x0,b
        add b,a         n5,b          ; T[i] + f (T[i+1] - T[i])
        tst b           a1,a          ; floor
        neg a ifmi                    ; sign
```

That is 14 single-word instructions, 15 with the `asl` that usually brings an
`x/16` argument to `x/8`.

**Clamp.** The clamp costs nothing: moving the accumulator to Y1 saturates any
value at or above 1.0 to `$7FFFFF`. That indexes entry 1,023 with the largest
fraction, which interpolates to the last entry.

**Scaled saturators.** For a saturator `g(m) = (C/2)·tanh(2m/C)` with the
ceiling a power of two below full scale:
1. Shift the magnitude so that 2m/C = 8 lands at 1.0.
2. Shift the interpolated result right before applying the sign. For example,
   `asr #5,a,a` gives a ceiling of full scale/32.

Both curves keep slope 1 at zero.

**Verification.** A kernel model of this lookup must reproduce three
behaviours: the limiter on the move to Y1, the truncation to the index and
fraction, and the floor of the interpolated value. Exercise the clamp in at
least one bit-exact case. If no knob setting reaches it, start a case from
out-of-range state. The emulator places internal X like any other memory, so
loading the table into the kernel runner's X memory is enough.

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
- **Every track costs at least ~3,000 cycles** (~3,100 measured on hardware):
  the time to send its 32-word block to DSP1. DSP2 waits for the previous transfer before the next one. Budget
  `sum(max(render + ~100, ~3,000))`, not the sum of renders (see the
  [voice-link slot floor](08-dsp2-voice-abi.md#the-voice-link-slot-floor)).
  - An **idle** track renders for 3,438 cycles (the fallback renderer's
    padding), about one transfer.
  - A cheaper machine on that track saves only the part above ~3,000.
- **Work in a track's slot is free up to the floor.** A machine that occupies
  two tracks gains from the second only by moving real work into that
  track's render; a second render that just writes zeros still costs a full
  slot.
  - **Balance:** split the work so both renders sit near ~3,000–3,500.
  - **Order:** the first track renders before the second in each pass. The
    first can produce the output from history (for example delay-line taps
    older than the block). The second then advances the state for the next
    block, with the hand-over kept in the first track's state block.
- Plan with the **worst block**, not the average: an envelope attack, all
  tracks triggered in the same block, maximum resonance, maximum pitch, all
  edges in one block.
- In the emulator, a heavy 16-voice kit around 62–63k cycles still meets the
  deadline, with about 11k cycles of slack left in DSP1. **Hardware is slower**:
  the emulator charges no external-memory wait states, and the machine code runs
  from external SRAM. Two calibration points so far:
  - A dual oscillator with no external data reads ran 16 instances on hardware
    at about 62.5k emulated.
  - A filter reading 128 table words per block from external memory hid
    roughly 730–1,560 extra hardware cycles per voice.
  - Measured directly: about 5 wait states per access and per code-fetch miss
    in area 3, and about 1 in areas 0–2
    ([memory maps](02-memory-maps.md#measured-wait-states-hardware-dsp2)).

  **Cost model** (validated on hardware, below):

  ```text
  hardware cycles ≈ emulated cycles + w × (external data accesses + executed code words)
  ```

  - **`w`:** the wait states of the memory involved: about 1 in area 2, where
    the kit's code region and delay pool sit, and about 5 in area 3.
  - **External data accesses:** count every X/Y access outside internal memory
    per block, including both moves of a dual X/Y move.
  - **Executed code words:** count each code word once per block, however
    often it runs. Sixteen voices share the 1K-word instruction cache, so a
    render starts cold and fetches each word it executes about once. Loops
    pay only on their first pass.

  Count both per render with a per-instruction trace (monitor `trace cpu`).
  One trap: an emulator that runs a one-instruction `do` loop as a single step
  reports that instruction's accesses once, not once per pass.

  **Validation (2026-10-04).** A load-meter machine on a spare track burned a
  set number of cycles per block, in steps of 512 (see below). The kit was a
  generator followed by seven chained two-track reverbs, all in area 2. Each
  pair made 550 external data accesses per block (342 reads, 208 writes) and
  executed about 715 code words in a typical block.
  - **First underrun:** 30 steps on hardware, 53 in the emulator.
  - **Gap:** about 11.8k cycles.
  - **Fixed part:** about 1,200 of that is stock code and the generator's cold
    fetches (the [calibration](02-memory-maps.md#measured-wait-states-hardware-dsp2)
    found stock code losing about 1,000 cycles).
  - **Per pair:** about 1,500 cycles.

  With every access at one wait state, the model predicts 1,265 per pair,
  about 16 % low. With one more wait state per **write**, it predicts 1,473,
  within about 40. The calibration timed only reads, so the extra write cost
  is a hypothesis; timing writes the same way would settle it. Until then,
  count writes twice:

  ```text
  hardware cycles ≈ emulated + w × (external reads + 2 × external writes + code words per block)
  ```

  Add the **pipeline interlocks** the emulator does not charge either
  ([above](#pipeline-interlocks)): up to 10–25 % in unscheduled inner loops.

  **Hardware budget (2026-10-05).** Kits of 14–15 identical machines plus a load
  meter put the usable total for the 16 track slots at **about 68k cycles per
  block** on hardware, with every slot costing at least **~3,100**. Fifteen
  plain oscillators, fifteen PWM oscillators with slide and seven two-track
  reverbs all left exactly the same headroom: they all sit on the floor.

  Count code words a typical block executes, not every word a long run ever
  touches. Start-up and rarely taken paths inflate the latter: here 1,020
  against 715.

  **Load meter.** To measure a kit's real headroom, put a machine on a spare
  track that burns N cycles per block in a one-instruction `do` loop, then
  outputs silence. The loop body is cache-resident and touches no memory, so
  it costs one cycle per pass on hardware too. Raise N until underruns start:
  N at the first underrun is the free budget. Comparing it with the
  emulator's threshold for the same kit gives the hardware-only cost directly.
  (A machine's packet updates on its track's trig, so trig the meter after
  each change.)

  Keep hot tables internal, leave margin, and test on hardware with the
  intended voice count.
- Measure a render's cost as the cycle count between the dispatcher's call
  (`P:0xB4`) and return (`P:0xB5`). Sum per track and compare the totals and
  the worst block with the budget.

| Machine | Measured (cycles/block) |
| --- | ---: |
| GND-SW | 734 default, 2,341 at note 127 with maximum ramp |
| NFX-GN | 154 |

As rough guidance from other custom machines built the same way:

Render costs only; each track still costs at least ~3,000 of the pass.

| Kind of machine | Cycles/block |
| --- | ---: |
| Modulated delay (chorus/flanger) | ~1,500 |
| Delay with filtered feedback (fast path) | ~1,750 |
| Filter with an envelope (internal tanh table, block-rate envelope) | ~2,700 |
| Dual oscillator with PWM and LFO | ~3,900 |
| 2× oversampled ladder filter with an envelope (internal tanh table, block-rate envelope) | ~3,500 |
