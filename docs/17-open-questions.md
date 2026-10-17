# Open questions and errata

## Open questions

| Question | Why it matters | How to settle it |
| --- | --- | --- |
| Why area 3 costs about 5 wait states when the BCR decodes to 4 ([measured](02-memory-maps.md#measured-wait-states-hardware-dsp2)) | Exact cost models | **Answered.** The DSP56303 technical data gives the external access time as (WS + 2) · Tc for 4 to 7 wait states, so a BCR value of 4 is 6 cycles (DSP56303 Technical Data, Table 2-8 item 100: tRC = tWC = (WS + 1)·Tc for 1–3 wait states, (WS + 2)·Tc for 4–7, (WS + 3)·Tc for 8 and more); the measurement is right and the model should charge WS + 2 |
| The 32-ROM branch with the area-2 reservation (`R = 0x190000`) | It leaves only `0x010600` units of ROM budget there; only 48-ROM has run with this layout on hardware | Boot-test that branch and load a small sample bank |
| Instruction-cache details beyond the model ([instruction cache](13-dsp-programming.md#instruction-cache)) | A missed word costs about 0.9–1 cycle in area 0, but one layout change measured about 0.4 per modelled miss. Burst fills or overlapped fetches may hide part of a miss | Time kernels of equal size that fit and do not fit eight sectors, alone and repeated on adjacent tracks |
| Descriptor display-format values other than `0` (hidden), `1` (plain 0–127) and `5` (signed −64…63), and the tail `+54..+85` ([display formats](07-machine-catalogue.md#display-format-nibbles)) | Custom display formats for knob values (e.g. stock `3` on GND-SIN's RAMP) | Give one knob each value 2–15 and observe the LCD |
| When exactly the OS reruns control handlers | Handlers with expensive work; tempo-synced parameters | Breakpoint the handler during sequencer playback, tempo changes, trigs |
| Maximum packet length | Machines with more than ~10 control words | Return larger counts and read back `Y:S…` |
| The extra free IDs (30–31, 40–47, 73–79, 86–94) on hardware, and under sequencer playback, copy/paste, sound save, randomize and song mode | They passed the static audit and an emulator differential test of assignment, kits, SysEx and the browser ([catalogue](07-machine-catalogue.md#free-ids)) | Boot-test a machine on a new ID; repeat the differential test with those operations |
| Why arithmetic and transfer stalls cost so little on hardware while AGU interlocks cost about their estimate ([pipeline interlocks](13-dsp-programming.md#pipeline-interlocks)) | Removing 253 estimated arithmetic and transfer stalls per reverb pair freed under about 130 cycles; removing about 64 AGU cycles per ladder instance freed 0–73. The arithmetic rules as applied may not match the DSP56300's actual forwarding | Time single kernels with and without one interlock kind on hardware, with the load meter's 4-cycle FINE steps |
| Raising the ESSI0 voice-link rate | 16 transfers × 3,200 = 51,200 of the 71,680 DSP2 cycles per block go to the link | **Not possible.** CRA0 `$180801` (PM = 1, PSR = 1) already drives SCK0 at Fcore/4, the ESSI's internal maximum, and DSP1 as the slave accepts at most Fcore/3 (DSP56303 User's Manual rev 1, section 7.2.3 note and section 7.5.1: an internally sourced ESSI clock must not exceed Fcore/4, an external one Fcore/3); by the register decode the word is 25 bit clocks because of the bit-length frame sync, 100 cycles |
| Why floor-bound kits leave less headroom on hardware (21.5k against 27.1k in the emulator) | Whether the hardware floor is about 3.48k per slot or the block is about 68k DSP cycles ([slot floor](08-dsp2-voice-abi.md#the-voice-link-slot-floor)) | **Answered.** DSP2 runs at 98.784 MHz (its program never writes PCTL), so its block is 71,680 cycles, and a link word is 100 cycles by the register decode; 15 floor slots then take 50,176 cycles, 3,345 per slot, within one meter step of the 3,290 measured on hardware. The codec-clock question stays open on its own |
| The kit examples' shifted family table (E12's record removed, P-I…RAM one slot up) on hardware | The [E12 memory layout](12-packing-firmware.md#removing-e12-instead-memory-without-a-reservation) works on hardware, but the build tested there renamed slot 3 instead of removing it; the shift has run only in the emulator | Flash a kit example; browse and assign from P-I, ROM and RAM; record with RAM-R1 |
| 32-ROM/2-RAM configuration with the pool reservation | Other Machinedrum variants | Boot-test that branch |
| The code-region reservation (`0x1B0000`) with RAM machines kept, on hardware | The layout for builds that keep E12; verified only in the emulator (the pool reservation `0x190000`, with RAM machines removed, has run on hardware) | Flash a reserving build (the examples before the E12 layout); record with RAM-R1..R4 and load a large sample bank |
| First-run flash programming by MainOS (`0x200140`, magic at `0x3FFE`) | Whether MainOS can rewrite the boot sector | **Answered: yes.** The routine runs from the platform init before the CS0 remap, reads flash word `0x3FFE`, and if it is not `0x224B` reprograms flash `0x0..0x39BF` word by word from the boot-sector copy embedded in MainOS (at MainOS + `0x481D0`, AMD word-program sequence), writes the magic, then parks on a restart prompt. It never runs on a flash that already carries the magic, which is why it has not triggered; it matters to anyone whose MainOS edit moves or alters the embedded copy |
| ColdFire task and interrupt structure (priorities, timer ISRs) | Only needed for deeper OS changes | Trace in the emulator |
| Kit record bytes `+0x1E0…+0x45F` | Kit tools | **Answered** in [kits](06-coldfire-os.md#kits): LFO records, master-effect blocks, trig groups, mute groups; the group bytes are unchecked indexes in two stock readers |
| Bit-exact models of the stock DSP1 effects | Offline rendering, new DSP1 effects | Instruction-level derivation, as done for the GND conversions |

## Errata to older notes

Earlier community notes on this firmware contain claims that later work
disproved. If you read older material, correct it as follows:

| Older claim | Correct |
| --- | --- |
| The CPU is an MCF5307 | MCF5206E (board model and MAME driver) |
| Blob checksum = sum of decompressed data | Sum of **compressed** bytes |
| The DSP blobs are S-record-like with tag 3 = P memory | Little-endian 24-bit word stream: preamble `3,entry,4,config`; sections tagged 0/1/2 = P/X/Y; terminator `3,entry` |
| DSP2 entry point is `P:0x22` | `P:0x24` (preamble and terminator both say so) |
| DSP2 loads synthesis engines into "overlay slots" `P:0x020F00–0x0C7FBD` from external memory through `X:0xFFFF80` | All machines are resident; no program writes occur on assignment or trig; the "slots" are zero-filled gaps in a flattened image |
| A 16-entry machine dispatch table at `P:0x101AC0` | That is instruction code read at the wrong boundary; the real tables are at `0x145AF5/0x145BB6/0x145C77`, 193 entries |
| P:/Y: tables at `0x145AF5` hold different values | One physical table seen through two aliases |
| Three dispatch stages are INIT / STEP / UPDATE-per-frame, and `Y:0x800+0x40·v` holds the machine type | Init (on type change), update (on trig), render (every block); `Y:S` is a **pending** marker cleared before update, the active type is `Y:0x153+t` |
| `P:0x103D7B` is a 20-entry TRX-UW function table | A 21-entry E12 sample descriptor table {start, length, 0} |
| The ColdFire sends MIDI over a UART to the DSPs' SCI port to play notes | MIDI UART1 is external MIDI; UART2 is the panel; the DSPs get control through HI08 |
| The audio links are ESAI / memory-mapped FIFOs | ESSI0 (DSP2↔DSP1) and ESSI1 (codec) with DMA |
| `0x1000000` scratch is shared memory between the CPU and the DSPs | ColdFire internal SRAM; the DSPs communicate through HI08/ESSI only |
| OS_A/OS_B are auxiliary operating systems | 512 KiB factory machine/kit databases |
| Custom flash data can be referenced at its file offset | Only through `0x10000000 + offset` after the CS0 remap |
| MCF registers at `0x300064/0x300068` are CS1 | They are CSAR0/CSMR0 (chip select 0, the boot flash) |
