# Glossary

| Term | Meaning |
| --- | --- |
| **Alias (CS0)** | Flash seen at `0x10000000 + offset` after the OS reprograms chip select 0. The only valid way to reference flash once MainOS runs. |
| **Alias (P/X/Y)** | DSP external SRAM appearing at the same address in the P, X and Y spaces |
| **Bank (DSP)** | A reserved `0x1000`-word region of DSP2 external memory holding one custom machine's code and tables |
| **Bank (flash)** | A region in the erased tail of the first flash megabyte holding custom descriptors, menus, handler code and tables |
| **Blob** | A compressed flash record: size, checksum, data |
| **Descriptor** | 86-byte ColdFire record defining a machine: handler, ID, name, labels, defaults |
| **Dispatch tables** | DSP2's three 193-entry pointer tables (init, update, render) indexed by DSP type |
| **DSP type** | Index into the dispatch tables; firmware ID + 1 for audio machines |
| **Family** | A kit-edit menu column (GND, TRX, …, RAM; NFX in the NFX-GN, NFX-SV and NFX-4P examples), defined by the family table |
| **Fallback** | Dispatch entry 0's routines: RTS for init/update, the padded silence renderer |
| **Firmware ID** | A machine's number in kits and the descriptor/ID tables (0–191) |
| **Handler** | ColdFire function converting raw knob words into DSP packet words |
| **HI08** | The DSP56303 host interface; the ColdFire's only path into DSP memory |
| **ESSI** | Enhanced synchronous serial interface: ESSI0 links the DSPs, ESSI1 the codec |
| **Neighbour tap** | Reading the previous track's raw voice block from the other output bank |
| **NFX** | Neighbour-effect family, added after RAM by the NFX-GN, NFX-SV and NFX-4P examples |
| **Packet** | Control words the handler produces, written to `Y:S+1…` |
| **Pending word** | `Y:S`, set to the DSP type by a trig; makes the dispatcher call update (and init on a type change) |
| **Delay pool** | Optional per-track ring memory for delay-line machines at DSP2 `0x190000–0x1AFFFF`, reserved from sample memory |
| **Code region** | DSP2 `0x1B0000–0x1BFFFF` (bus area 2), reserved from sample memory for custom machine code |
| **E12 region** | DSP2 `0x103DBA–0x135205` (bus area 0), E12's sample data; with E12 removed it holds a delay pool at `0x104000` and program banks from `0x124000`, with sample memory stock. The examples' layout. |
| **Producer pass** | DSP2's per-block loop over the 16 tracks |
| **Q23** | Signed 24-bit fraction, `w / 2^23` |
| **Raw word** | 16-bit knob value passed to a handler, about `value << 7`, slewed by the OS |
| **S** | A track's DSP2 state block base, `Y:0x800 + 0x40·t` |
| **Stale DAC read** | A DAC buffer word sent to the codec twice without being rewritten: the signature of a missed block |
| **UW** | "User Wave": the sample-playback model (ROM/RAM machines, sample memory) |
