# Writing a custom machine

This walkthrough builds **NFX-GN**, a neighbour gain effect, from scratch. It is
deliberately tiny, so every part of the contract is visible. The same steps
scale to the real machines in the [reference](14-custom-machine-reference.md).

The complete, buildable version is in
[`tools/examples/nfx-gn`](../tools/examples/nfx-gn/). A second example,
[`tools/examples/gnd-sw`](../tools/examples/gnd-sw/), adds the GND-SW PolyBLEP
saw as a fifth GND machine. Both keep every stock machine, RAM recording
included. Both images were verified in the emulator: registration,
front-panel selection, bit-exact audio, and (on the GND-SW image) RAM recording
still working (**Verified**; not yet flashed to hardware).

## 1. Decide the identity

| Decision | NFX-GN | Constraints |
| --- | --- | --- |
| Firmware ID | 15 | Use a free ID: 4–15, 30–31, 40–47, 73–79 or 86–94 (see [catalogue](07-machine-catalogue.md#free-ids)). DSP type = ID + 1 = 16, which must still be unused. |
| Family | NFX, a new eleventh family | Existing family: append to its menu (as GND-SW does). A new family means relocating the family table. |
| Name | `NFX-GN` | 3-character family + 2-character suffix |
| Knobs | GAIN (default 64) | Up to 8 labels of 4 characters |
| DSP bank | `P:0x1BA000`, 4096 words | Inside the reserved code region `0x1B0000–0x1BFFFF` (see [reserving sample memory](12-packing-firmware.md#reserving-sample-memory)) |
| Memory | Packet `+1`, no state, no pool | |

## 2. Design the packet and state

Decide what the ColdFire computes and what the DSP computes. Everything that is
per-knob (curves, tables, divisions) belongs on the ColdFire. The DSP gets
ready-to-use numbers.

| Word | Contents | Written by |
| --- | --- | --- |
| `+0` | Pending type | OS |
| `+1` | Gain / 2 as Q23: `0x400000` = unity, up to `0x7F0000` ≈ 1.98× | Handler |

For a real machine, write this table first. Include every private state word,
its reset value, and which stage (init/update/render) resets it.

## 3. DSP2 code

Assembled with an absolute-origin DSP56300 assembler (see
[DSP programming](13-dsp-programming.md#toolchain)). `sdk.inc` defines
`SDK_BANK equ $1BA000`.

```asm
        include "sdk.inc"
        org     p:SDK_BANK

machine_init:                   ; type changed: no private state to reset
        rts

machine_update:                 ; trig: nothing to restart
        rts

machine_render:                 ; R6 = S, R7 = output bank (M7 = $1F)
        move    y:$142,a        ; track index
        tst     a
        jeq     silent          ; track 0 has no neighbour
        move    y:(r6+1),x1     ; gain/2, Q23
        move    y:$140,a        ; this voice's output bank...
        eor     #$20,a          ; ...the other one holds track t-1
        move    a1,r0
        do      #32,gain_end
        move    y:(r0)+,y0      ; neighbour sample
        mpy     x1,y0,a         ; x * gain/2
        asl     a               ; * 2
        move    a,y:(r7)+       ; store with limiting (saturates)
gain_end:
        rts

silent:
        clr     a
        rep     #32
        move    a,y:(r7)+
        rts
```

Checklist for any render routine:

- [ ] Exactly 32 writes through `(r7)+`, on every path, including the track-0 path.
- [ ] Nothing written outside the bank, the machine's own state words and its
      own pool slice.
- [ ] Any `Mn` changed from linear is restored before `rts`. The same goes for
      SR/OMR mode bits.
- [ ] `DO` loop labels sit on the instruction **after** the loop body.
- [ ] No `Nn` or `Mn` value assumed that you did not set (except `M7`).
- [ ] The neighbour bank is only read, never written.

The optimized version would move the load into the multiply's parallel move and
software-pipeline the loop. Correct first, then fast (see
[DSP programming](13-dsp-programming.md)).

## 4. ColdFire control handler

GNU assembler for the MCF5206E (`m68k-linux-gnu-as -m5206e`):

```asm
        .text
        .global gain_control
| int gain_control(u32 *packet, const u16 *raw)
gain_control:
        move.l  4(%sp),%a0          | packet (32-bit words)
        move.l  8(%sp),%a1          | raw knob words (16-bit, about value << 7)
        moveq   #0,%d0
        move.w  (%a1),%d0           | knob 1: GAIN
        cmp.l   #64,%d0             | below half a knob step: exact zero
        bcc.s   1f
        moveq   #0,%d0
1:      lsl.l   #8,%d0              | raw << 9: 8192 (knob 64) -> 0x400000
        lsl.l   #1,%d0
        move.l  %d0,4(%a0)          | packet[1] -> Y:S+1
        moveq   #2,%d0              | two words: type + gain
        rts
```

Link it at the **CPU alias address** where it will live in flash, and extract
the raw bytes:

```sh
m68k-linux-gnu-as -m5206e -o control.o control.s
m68k-linux-gnu-ld -Ttext=0x100FF200 -e gain_control -o control.elf control.o
m68k-linux-gnu-objcopy -O binary control.elf control.bin
m68k-linux-gnu-nm control.elf       # symbol addresses, for the descriptor
```

`0x100FF200` is file offset `0xFF200` seen through the CS0 alias: the example
places its code 0x200 bytes into a flash bank at `0xFF000`. Pick the offset
from your flash-bank layout (see [packing](12-packing-firmware.md#flash-banks)).
The examples link the two sample-loader guards
([`tools/examples/common/guards.s`](../tools/examples/common/guards.s)) into the
same blob. The DSP bank lies in stock sample memory, which must be reserved
before custom code can live there.

## 5. Descriptor

Start from the stock GND-SIN descriptor (MainOS `0x24EFAA`, 86 bytes) and
overwrite:

```python
d = bytearray(mainos[0x4EFAA:0x4EFAA + 86])
d[0:4]   = (0x100FF200).to_bytes(4, "big")     # handler (gain_control), CPU alias address
d[4]     = 15                                  # firmware ID
d[5:8]   = b'NFX'                              # family label
d[8:10]  = b'GN'                               # suffix
labels   = ['GAIN', '', '', '', '', '', '', '']
for i, l in enumerate(labels):
    d[10 + 4*i:14 + 4*i] = l.encode().ljust(4, b'\0')
d[42:50] = bytes([64, 0, 0, 0, 0, 0, 0, 0])     # defaults
d[50:54] = bytes.fromhex('11111111')           # format bytes
```

## 6. Register it

In the image:

1. ID table: MainOS word `0x252092 + 4·15`, `0x24EF54` → descriptor's CPU alias
   address.
2. Menu: a stock image has no NFX family. The example copies the family table
   to its flash bank, appends `NFX` with a one-entry menu `[NFX-GN, 0]`, and
   repoints the eight references to the table
   (see [adding a family](07-machine-catalogue.md#adding-a-family)). All ten
   stock families stay. A machine for an existing family instead gets a longer
   copy of that family's menu (GND-SW: the four stock entries, then GND-SW).
3. DSP2 dispatch: set type 16's cells to the entry symbols.
   - `Y:0x145AF5 + 16` = `machine_init`
   - `Y:0x145BB6 + 16` = `machine_update`
   - `Y:0x145C77 + 16` = `machine_render`

   Check first that each cell still holds the fallback value (equal to cell 0).
4. DSP2 upload: insert a P section `0, 0x1BA000, n, code…` before the stream
   terminator.
5. Reserve the code region: lower the two startup sample budgets so sample
   memory ends at `0x1B0000`, and install the loader guards with that ceiling.
   RAM machines keep working with less recording memory.

[Packing firmware](12-packing-firmware.md) gives the full procedure and checks;
`tools/examples/nfx-gn/build.py` performs all of it with `mdkit`.

## 7. Verify before hardware

A machine is ready for hardware when all of these pass in the emulator. The
examples' `verify.py` scripts do steps 1 and 3 with the kit's
[emulator](18-emulator.md): `md-kernel` for the kernel, the monitor for the
booted image.

1. **Bit-exact kernel test.** Run the DSP code in a DSP56300 emulator with
   random packets, random neighbour input and random pre-existing state
   (poisoned registers and memory). Compare every output sample and every
   private state word with an integer reference model. For NFX-GN the model is:

   ```python
   y = clamp((x * g) >> 22, -2**23, 2**23 - 1)      # g = packet word +1
   ```

2. **Handler test.** Feed the handler every raw value 0…16256 (and the slew
   values in between). Compare with the model's intended packet.
3. **Booted image test.** Boot the packed image, then:
   - check the descriptor, menu and dispatch cells in live memory;
   - select the machine **through the front panel**;
   - compare the live packets and audio against the model;
   - check that no memory outside the machine's own regions changed.
4. **Budget test.** Run the heaviest settings on as many tracks as you intend to
   support, with other machines. Check that no DAC half is stale and no block
   is missed (see [emulation and verification](16-emulation-and-verification.md)).
   Keep the emulator's missing wait states in mind.
5. **CS0 check.** Scan the image for pointers into the low flash window.

Then flash, starting with one instance and adding instances until the budget
you tested.

## Generators instead of effects

A generator ignores the neighbour bank and keeps phase and envelope state in its
private words. The typical skeleton:

```text
init:    clear private state (phase, envelopes, filter memories)
update:  trig: reset phase? restart envelopes (decide and document)
render:
    load packet words and state into registers
    loop 32:
        advance phase, compute sample, apply envelope
        store sample >> 2 (quarter scale, like stock generators)
    store state back
```

GND-SW is a complete example: see the
[reference](14-custom-machine-reference.md#gnd-sw-polyblep-saw) and
[`tools/examples/gnd-sw`](../tools/examples/gnd-sw/).
