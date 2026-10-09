# DSP2 voice ABI

This is the contract between DSP2's resident dispatcher and a machine's DSP code.
It was recovered from the dispatcher (`P:0x64–0xE1`) and checked with
breakpoints at every stage entry and return. The kit's example machines rely
on all of it.

## Dispatch and lifetime

DSP2's main loop (`P:0x64`) runs one **producer pass** per audio block. With
`S = 0x800 + 0x40·t`:

```text
for t in 0..15:
    S = 0x800 + 0x40*t;  Y[0x141] = S;  Y[0x142] = t
    pending = Y[S]
    if pending != 0:
        if pending != active[t]:            # active[t] = Y[0x153 + t]
            active[t] = pending             # stored BEFORE init   (P:0x8B)
            call init_table[pending]        # call site P:0x8D
        Y[S] = 0                            # cleared BEFORE update (P:0x97)
        call update_table[active[t]]        # call site P:0x98
    Y[0x140] ^= 0x20                        # alternate between Y:0x100 and Y:0x120
    call render_table[active[t]](R7 = Y[0x140])   # call site P:0xB4
    if t == 0: wait for DSP1's block sync (PDRC bit 1), start DMA2   # P:0xB9-0xCD
    wait for the previous voice's DMA0 to finish   # P:0xCF
    start DMA0 from Y[0x140] to ESSI0             # P:0xD1-0xD3
```

| Table | Address | Called |
| --- | --- | --- |
| Init | `Y:0x145AF5 + type` | When a trig brings a type different from the active one |
| Update | `Y:0x145BB6 + type` | On every trig (pending word nonzero), after init if init ran |
| Render | `Y:0x145C77 + type` | **Every block**, for every track, whether or not anything changed |

Each table has 193 entries (types 0–192). Unused types point at the fallback
entries (type 0's values): init/update `P:0x10008E` (RTS) and render
`P:0x10008F` (writes 32 zeros after a deliberate padding loop).

Lifetime facts:

- **Update means "trig".** The OS writes the type into `Y:S` only for a trig.
  Knob changes rewrite the packet words `Y:S+1…` without setting it.
- **Init runs only on a type change.** Retriggering the same machine skips init.
  Init must cope with whatever the previous machine left in the 64-word block:
  the dispatcher never clears it.
- **Render runs even without a trig.** A newly assigned machine renders the old
  type's state until its first trig. There is no note-off, no destructor and no
  "voice finished" callback.
- **Assignment latches.** A kit reassignment does not change `active[t]`; the
  old DSP code keeps rendering until the next trig. Removing custom code
  therefore requires clearing matching `active[t]`/pending cells, or retriggering.
- `Y:S` itself belongs to the dispatcher and the host protocol. Never use it as
  state.

## Entry registers

Calls are `JSR` through the tables; return with `RTS`.

| Stage | On entry | Modifiers | Returns to |
| --- | --- | --- | --- |
| Init | `R6 = S`, `R1 = t`, `R0 = new type`, `R2 = target` | `M0–M7 = 0xFFFFFF` (linear) | `P:0x8E` |
| Update | `R6 = S`, `R0 = active type`, `R1 = target`, `X0 = 0` | Linear if init ran; otherwise as set at `P:0x74` (linear) | `P:0x99` |
| Render | `R6 = S`, `R0 = type`, `R1 = target`, **`R7 = output bank`** | `M0–M5 = 0xFFFFFF`, **`M7 = 0x1F`**, **M6 inherited** | `P:0xB5` |

- `N0–N7` are **not** initialized. Never rely on an `Nn` you did not load.
- Render inherits `M6` from earlier code. Set it if you use R6 with an addressing
  mode that `M6` affects. A plain `(r6+n)` displacement is safe with `M6` linear,
  but do not assume it is linear.
- The dispatcher reloads everything it needs from memory between stages. A, B,
  X, Y, R0–R7 and N0–N7 are scratch.
- **Restore** anything global you change:
  - modifier registers left non-linear (`Mn`), because the next machine may
    assume linear;
  - SR mode bits (scaling, rounding, saturation);
  - OMR;
  - interrupt and peripheral configuration.

  `RTS` does not restore SR.
- Balance the hardware stack and finish every `DO`/`REP` loop before returning.
- There is no return value.

Interrupts (host transfers, DMA, ESSI) can run inside your code. Do not assume
exclusive access to peripherals, and keep interrupt latency in mind in very
long uninterruptible sections (`REP`, long `DO` bodies are fine).

## The state block

`Y:S … Y:S+0x3F`, 64 words per track:

| Offset | Owner |
| --- | --- |
| `+0` | Dispatcher/host: pending type marker |
| `+1 … +(n−1)` | **Packet**: control words written by the ColdFire handler (`n` = handler's return count) |
| `+n … +0x3F` | Machine-private state: phases, envelopes, filter memories, ring indices |

The block persists across blocks and trigs, and across machine changes. It is
only reset by your own init.

Conventions used by all custom machines:

- The packet is at `+1…` (one word per converted knob, more if the handler
  needs them; up to `+10` has been used), private state from the next word up.
- Init clears exactly the private words the machine relies on.
- Update resets what a trig should reset (envelopes, phases) and leaves the rest.
- Private state words can be read and written by host tests, which is useful for
  bit-exact verification (see [emulation and verification](16-emulation-and-verification.md)).

## Output

Render must write **exactly 32 signed 24-bit samples** into the bank given in
`R7` (`Y:0x100–0x11F` or `Y:0x120–0x13F`), normally with 32 × `move …,y:(r7)+`
(`M7 = 0x1F` wraps the pointer back). Requirements:

- Write all 32 samples every call, zeros included. A silent machine must still
  write silence.
- Do not write outside the bank, and do not change `Y:0x140`. The dispatcher
  starts the DMA from `Y:0x140`, not from R7.
- All samples must be complete on return. DMA0 reads the bank while the next
  voice renders.
- Output is mono. DSP1 applies track effects, level, pan and routing.

**Level.** Stock generators leave headroom: GND-SIN's oscillator is shifted
right by two (about quarter scale) before its envelope. The custom generators
follow that convention (`>> 2` for a single oscillator), and the effects keep
unity gain at their neutral settings. How much of DSP1's chain (track EQ,
filter, distortion, mixer) tolerates full-scale voices has not been measured;
matching the stock level is the safe choice.

## The neighbour tap

Because the output banks alternate, at render entry the **other** bank holds the
previous track's output **from the same audio block**:

```text
current  = Y[0x140]            # == R7 at entry
previous = current ^ 0x20      # Y:0x100 <-> Y:0x120
input[i] = Y[previous + i]     # track t-1's raw voice, i = 0..31
```

This is the input of every neighbour (NFX) machine. Properties:

- It is the **raw** DSP2 voice, before the source track's DSP1 effects, level,
  pan and sends.
- Reading is safe; DMA0 is reading the same bank. **Never write it.**
- Chains work without extra latency: a neighbour machine on track 3 reads one on
  track 2, which read track 1, all in the same block.
- **Track 0** has no previous track: its "previous" bank holds track 15 of the
  previous block. A neighbour machine should output silence on track 0 (check
  `Y:0x142 == 0`) rather than feeding back.
- Only the immediately preceding track is available. A second earlier track
  would need its own capture.

## Memory a machine may use

| Region | Rule |
| --- | --- |
| Registers | All scratch, subject to the restore list above |
| `Y:S+n…+0x3F` | Your private state |
| Your P bank (e.g. `0x127000–0x127FFF`) | Code, and constant tables read through the Y alias |
| `X:0x000–0x01F` | Scratch **within one call** (stock renderers use it; nothing survives between calls) |
| The delay-pool slice `0x190000 + 0x2000·t` (or `0x104000 + 0x2000·t` in the [E12 layout](02-memory-maps.md#custom-machine-regions)) | Per-track ring memory, in builds that have a delay pool |
| Sine table `X/Y:0x148000` | Read-only, 32,768 entries |
| Internal `X:0x257–0x6FF` | Read-only tables uploaded with the DSP2 stream, shared by convention; the shared tanh table is `X:0x280–0x680` (see [DSP programming](13-dsp-programming.md#internal-memory-tables)). Never written at run time. |
| Everything else | Not yours: dispatch tables, `Y:0x140–0x162`, other tracks' blocks, sample memory, DMA buffers, MMIO |

Rules for pool memory:

- Index by track so instances never share a ring.
- Keep a valid-sample counter instead of clearing thousands of words on init;
  return zero for history that has not been written yet.
- Wrap with modulo addressing or explicit masking. Restore `Mn` to linear before
  returning.

## Timing

The whole producer pass (16 renders plus dispatch, updates, interrupts and DMA
waits) must fit in **73,728 cycles**. Planning numbers from the emulator:

| Item | Cycles per block |
| --- | --- |
| Idle track (fallback renderer, NOP padding) | 3,438 |
| GND-SIN | ~700 |
| Stock TRX/EFM/E12/P-I voices | ~2,000–3,200 |
| GND-SW (example) | 734 default, 2,341 worst |
| NFX-GN (example) | 154 |
| NFX-SV (example) | 2,572–2,924 |
| NFX-4P (example) | 2,931–3,469 |

### The voice-link slot floor

A render's cost is not the whole story. The link that carries each track's
block to DSP1 sets a minimum time per track.

**The transfer.** DSP2 sends each 32-word track block over ESSI0 with DMA0. DSP2
clocks the link (CRA0 `$180801`, CRB0 `$33138`: 24-bit words, internal clock,
network mode). One word takes **96 DSP cycles**, so a block takes
**32 × 96 = 3,072 cycles**. DSP1 receives with DMA4 into its 512-word ring
`Y:0x600–0x7FF`.

**The double buffer.** The dispatcher has two output buffers, `Y:0x100` and
`Y:0x120`, and alternates between them (`P:0x9B–0xA7`). After each render:

1. `P:0xCF` (`jset #23,x:DCR0,*`) spins while DMA0 is still sending the
   **previous** track's block.
2. `P:0xD1–0xD3` points DMA0 at this track's buffer and starts it.
3. The next track renders into the other buffer while this one is sent.

Track k's transfer runs during track k+1's render, so each track takes

    slot = max(render + ~40, 3,072)

**Below the floor.** A render shorter than the transfer leaves DSP2 spinning at
`P:0xCF` for the difference. That time is lost:
- **Nothing is banked.** With two buffers, DSP2 can be at most one block ahead
  of the link. A short slot's leftover time cannot help a later, longer render.
- **Long renders idle the link.** While a render runs longer than 3,072 cycles,
  the link finishes the previous block and sits idle.

Breakpoints at `P:0xB4`, `P:0xB5` and `P:0xD1` in the emulator show the
pattern. Its measured slots come out slightly under the nominal 3,072:

| Track | Render | Wait for the link | Slot |
| --- | ---: | ---: | ---: |
| ~700-cycle source after a ~6,400-cycle voice | 796 | ~2,380 | ~3,300 |
| 44-cycle render (32 zeros) after a ~6,400-cycle voice | 44 | ~2,850 | ~2,990 |
| ~3,300-cycle render | ~3,300 | 12 | ~3,400 |

Here the slot is the time from one transfer start to the next. So every track
costs at least one transfer, however cheap its render.

**Pass start.** After track 0's render, `P:0xB9–0xBF` polls port C bit 1
(`PDRC`) until DSP1 toggles it, and starts the master-return DMA2 there. Each
pass is aligned to DSP1's block.

**The maximum.** A block is 32 frames × 2,304 = **73,728 DSP cycles** (101.6064
MHz, the emulator's clock).
- **Link time:** the 16 transfers take 16 × 3,072 = **49,152 cycles**, two
  thirds of the block. That is the shortest possible pass.
- **Render room:** what is left, about 24.6k cycles less dispatch, sync and
  interrupts, is all the render time a kit has above the floor. One track can
  take it, or many can share it.
- **Budget:**

      sum over the 16 tracks of max(render + ~40, 3,072) + overhead  <=  73,728

  not the sum of the renders.
- **The deadline:** DSP1 processes each track as it arrives (`P:0x73` waits on
  DMA4's progress). If the pass runs past the block, DSP1 misses its DAC buffer
  swap and the output underruns.
- **The load meter** measures this room directly. With the other 15 tracks on
  the floor, the emulator gives SYN-LM 27.1k cycles (first underrun at 53
  steps of 512). That is (73,728 − 27.1k) / 15 ≈ **3.11k per slot**: the
  transfer plus about 40 cycles of dispatch.

The fallback renderer's 3,438-cycle padding is a little over one transfer. A
kit of cheap renders gains nothing below the floor. A machine needing more than
one slot's worth gains from a second track only if it puts real work into that
track's render (see [DSP programming](13-dsp-programming.md#budgeting)).

**Measured kits.**
- Kits whose render calls sum to about 62.5k cycles have met the deadline in
  the emulator.
- A kit whose renders summed to only 50k missed about 3.5 % of blocks. Its
  seven ~6,400-cycle voices were each followed by a 44-cycle one, and every
  short one still cost a full transfer slot.

**On hardware**, four floor-bound kits left exactly the same load-meter
headroom: **42 steps, about 21.5k cycles**.
- **2026-10-05:** fifteen plain oscillators, fifteen PWM oscillators, and seven
  two-track reverbs with a generator.
- **2026-10-09:** a generator and fourteen 1,760-cycle delays. The emulator's
  threshold for that kit is 53 steps.

So 15 floor slots take about 52.2k cycles on hardware, against 46.6k in the
emulator. One meter reading cannot tell which of two causes is at work:

| Reading | Per slot | Block |
| --- | ---: | ---: |
| The block is 73,728 cycles on hardware too; each floor slot costs about 370 cycles more than in the emulator | about 3.48k | 73,728 |
| The slot costs what it does in the emulator, but the block holds fewer DSP cycles: the DSPs run at about 94 MHz and the codec rate is set from outside | about 3.11k | about 68.3k |

DSP1 decides at boot where the codec clock comes from. `P:0x100072–0x10007F`
samples port D bits 2–3 about 4,000 times.
- **Toggling input:** DSP1 takes the external-clock setup (CRB1 `$3E08`).
- **Steady input:** DSP1 drives the codec clock itself (CRA1 `$201811`, CRB1
  `$3E3C`). The block is then 73,728 cycles at any clock.

Which path the hardware takes is
[not yet known](17-open-questions.md). The "about 68k usable per block" used in
earlier hardware figures is the second reading. Either way, the 15 floor slots
of a full kit take about 52k cycles on hardware.

The safe total on hardware is lower: the emulator charges no external-memory
wait states and no pipeline interlocks. Leave generous headroom and measure worst cases, not averages.
