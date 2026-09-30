# Emulation and verification

## The emulator

The findings and the custom machines were developed on an instrumented build
of **Gearmulator's Machinedrum board model**:

- a ColdFire MCF5206E core (a Musashi fork with ColdFire support);
- two DSP56303 cores from the dsp56300 emulator (JIT);
- the board: memory map, SIM peripherals, HI08, ESSI, DMA, codec, panel UART
  and LCD.

It boots the unmodified firmware from reset. The bootloader's own
decompression and DSP uploads run in the emulated CPU; nothing is preloaded.

The instrumentation adds:

- breakpoints, watchpoints and instruction/memory traces on all three
  processors;
- per-voice render cycle accounting (the span between `P:0xB4` and `P:0xB5`);
- DAC freshness checks (below);
- PC timestamp logs across processors;
- MIDI, panel-button and audio-input injection;
- LCD capture;
- multichannel audio recording.

### Where the emulator is not the hardware

| Difference | Effect | Mitigation |
| --- | --- | --- |
| **No external-memory wait states.** The cycle function accepts a wait-state count but never applies it; the bus-control register is not modelled. | DSP cycle counts are optimistic, most of all for code and tables in external SRAM (all machine code). | Leave headroom; test voice counts on hardware; cycle-ceiling tests. |
| **No instruction cache model.** DSP2 enables its cache at boot (SR CE bit, then `pflush`); the emulator has a cache model that is never used for fetches or timing. | Hardware penalizes cache misses and rewards tight loops; the emulator does neither. Runtime-written P code would run stale on hardware until `pflush`, but fresh in the emulator. | Keep inner loops compact; never rely on writing P memory at runtime. |
| **Flash visible at both `0x0` and `0x10000000`.** CSAR0 is ignored. | Images with low-window flash pointers boot in the emulator and **hang on hardware**. | Link and point through `0x10000000`; scan images for low pointers; optionally watch reads of `0x0–0xFFFFF` after the remap (`0x2002F2`). |
| **P/X/Y alias from `0x20000`** | Real decode depends on the address-attribute registers, which were not read from hardware. | Addresses used so far behave the same on hardware. |
| **Coarse CPU/DSP interleave** | See the next section. | Use a 30 µs quantum. |
| **Panel task-list workaround** | The board model periodically updates a firmware task list to make panel progress; it is not pure peripheral emulation. | None needed for machine work. |

### Interleave quantum

The board model runs the ColdFire and both DSPs in turn, each for a time quantum.
A DSP catches up to the CPU when the CPU touches HI08, but not the other way
round.

DSP1's host-command ISR busy-waits inside the interrupt for the CPU's second
word (see [DSP1 audio path](09-dsp1-audio-path.md#block-scheduler)). If the
CPU's slice ends between its two writes, DSP1 spins for up to a whole quantum.
Its block then ends after the exact-match block boundary, and one block is
missed.

With the original **125 µs** quantum (about 12.7k DSP cycles) this produced
occasional single missed blocks at modest loads. That looks exactly like a cycle
overrun: 192 stale DAC reads and a doubled mixer period. With a **30 µs**
quantum the longest ISR wait drops to about 3k cycles. The false misses vanish,
and emulator speed is unchanged.

A lone missed block at a moderate voice total is suspect. Check whether the
host-ISR wait (`P:0x9AA` → `P:0x9B1` on DSP1) was near one quantum before
concluding the kit is over budget.

## Verification methodology

### Bit-exact oracles

Every custom machine has an **integer reference model** in Python that
reproduces the DSP arithmetic exactly:

- accumulator widths;
- rounding (`rnd` for `macr`);
- limiting on stores;
- fractional remainders;
- table lookups.

A native test driver runs the assembled kernel in the DSP emulator **without
the firmware**:

1. Load the program words at the bank.
2. Write a packet and state block into Y memory.
3. Put neighbour input into the other output bank.
4. Set R6/R7/M7 as the dispatcher does, and call init/update/render.
5. Read back the 32 samples and the state.

The model must match **every sample and every state word**, across:

- random packets, random input and random prior state (poisoned registers and
  memory, to catch unset registers);
- tracks 0, 1 and 15;
- trigs mid-stream;
- both DSP engines (interpreter and JIT).

Handler models are checked separately against the ColdFire code over the full
raw range.

### Booted-image verification

Boot the packed image and check, through the real firmware paths:

1. The live descriptors, menus, family table, ID table and dispatch cells match
   the build manifest.
2. **Front-panel selection** reaches the machine, and the stored kit ID
   updates. Use real button events, not only SysEx.
3. Live packets equal the handler model for a set of knob settings. After each
   packet, the live audio and state equal the kernel model, block by block,
   using the real neighbour input.
4. No writes outside the machine's regions; no low-window flash reads
   (CS0 detector).
5. Stock machines still produce identical output.

### Deadline tests

Load realistic and worst-case kits (for example 4, 15 or 16 heavy instances,
mixed with other machines). Trig them together repeatedly, then measure over
about one second:

| Measure | Pass |
| --- | --- |
| Writes into the DAC half being played | 0 |
| **Stale DAC reads**: a DAC word DMA'd twice without being rewritten | 0 |
| DAC reads in the window | About 6 × 44,100 (a few frames' tolerance) |
| Mixer block period (DSP1 `P:0x4F` to `P:0x4F`) | About 73,728; one missed block shows as about 147k |
| Per-track render cycles (sum and worst block) | Report; compare with headroom targets |

Stale reads are the decisive symptom. The average render sum alone is not.

### Differential kernel tests

An optimized kernel is tested against its reference kernel. Both run on the
same random stimulus in both DSP engines, with every output sample, the whole
voice state, any scratch, and neighbouring memory (the pool, other tracks)
compared. Add explicit **cycle ceilings** so a fast path cannot silently turn
off.

## Hardware lessons

| Symptom on hardware | Cause | Emulator blind spot |
| --- | --- | --- |
| Boot hangs after the splash; kit edit freezes after a soft reset | Custom descriptors, menus and handler code referenced at low flash addresses; the OS remaps flash to `0x10000000` | Both windows mapped |
| Crackles, then all panel LEDs lit, then silence, with three custom effects (a chorus and two delays) | A signed control word disabled a delay machine's fast path: 3.2× the cycles, DSP2 overrun | No wait states; output bit-identical |
| After the fix, the same kit plus six more delay instances ran clean | Fast path restored, output bit-identical | Cycle-ceiling tests now guard it |

Test order for a new image:

1. Boot, splash, kit edit.
2. Select each new machine.
3. One instance, then the intended maximum.
4. Mixed with stock machines.
5. Leave it running.
