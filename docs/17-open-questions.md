# Open questions and errata

## Open questions

| Question | Why it matters | How to settle it |
| --- | --- | --- |
| Real DSP2 wait states and bus configuration (BCR, AAR0–3) | Converts emulator cycles into hardware cycles; sets the real voice limits | Read the registers on hardware (a tiny machine that copies them into its output), or measure a calibrated loop's cost on hardware against the emulator |
| Instruction-cache behaviour of machine code | Tight loops may be nearly free of wait states and branchy code expensive | Hardware timing of test kernels with different code footprints |
| DSP56303 internal memory configuration (memory-switch mode, cache) and whether `P:0x400–0x4FF` is free on hardware | Faster internal code space for hot loops | Read OMR/SR on hardware; test a machine placed there |
| Top of DSP2 external SRAM and banks `0x1FA000–0x1FFFFF` | More room for code and tables | Place a canary machine there on hardware |
| Descriptor "format" bytes `+50..+53` and the tail `+54..+85` | Custom display formats for knob values | Vary the bytes and observe the LCD |
| When exactly the OS reruns control handlers | Handlers with expensive work; tempo-synced parameters | Breakpoint the handler during sequencer playback, tempo changes, trigs |
| Maximum packet length | Machines with more than ~10 control words | Return larger counts and read back `Y:S…` |
| Unused IDs outside 4–15 (for example 30, 31, 40–47) | More than 12 custom machines | Audit every reader of the ID table and the sysex/UW mappings |
| Purpose of the fallback renderer's NOP padding | Removing it would free ~1.5k cycles per idle track | Look for timing dependencies (DMA, ESSI) with the padding shortened on hardware |
| 32-ROM/2-RAM configuration with the pool reservation | Other Machinedrum variants | Boot-test that branch |
| The code-region reservation (`0x1F0000`) with RAM machines kept, on hardware | The recommended layout for custom machines; verified only in the emulator | Flash the GND-SW example; record with RAM-R1..R4 and load a large sample bank |
| First-run flash programming by MainOS (`0x200140`, magic at `0x3FFE`) | Whether MainOS can rewrite the boot sector | Static analysis of that routine; it has never triggered with these images |
| ColdFire task and interrupt structure (priorities, timer ISRs) | Only needed for deeper OS changes | Trace in the emulator |
| Kit record bytes `+0x1E0…+0x45F` | Kit tools | Diff kits after targeted edits |
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
