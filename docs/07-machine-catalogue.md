# Machine catalogue structures

These are the ColdFire-side structures that make a machine exist: a descriptor,
an entry in the ID table, membership in a family menu, and a control handler.
All addresses are MainOS CPU addresses unless stated otherwise.

## Three numbering schemes

| Number | Range | Used by |
| --- | --- | --- |
| **Firmware machine ID** | 0–191 | Kits (`+0x1A0 + 4·t`), the ID table, descriptor byte `+4` |
| **DSP type** | 0–192 | DSP2 dispatch tables. For every audio machine, **type = ID + 1**. Type 0 is the fallback slot. |
| **SysEx model / UW selector** | per the manual | The machine-assignment SysEx |

Adding a dispatch-table entry does not make a machine selectable; adding a
descriptor does not give it DSP code. Both sides must be done.

## Descriptor (86 bytes)

The stock table holds 135 consecutive descriptors at `0x24EF54` (the first is
the empty machine `GND---`). A descriptor may live anywhere the OS can read,
including flash banks (through the CS0 alias).

| Offset | Size | Field |
| --- | --- | --- |
| `+0` | 4 | **Control handler** address, big-endian (ColdFire code) |
| `+4` | 1 | Firmware machine ID |
| `+5` | 3 | Family label, e.g. `GND`, `TRX`, `NFX` (ASCII, no NUL) |
| `+8` | 2 | Machine suffix, e.g. `SN`, `DL`. The UI shows `FAM-SX`. |
| `+10` | 32 | Eight parameter labels, 4 bytes each, NUL-padded (uppercase, digits, space, `-`) |
| `+42` | 8 | Eight default parameter values, 0–127 |
| `+50` | 4 | "Format" bytes. Stock values vary per machine (e.g. `11 31 33 31` for EFM-BD); all custom machines use `11 11 11 11`. **Hypothesis**: one nibble per knob selecting the value display format. |
| `+54` | 32 | Not interpreted. Custom descriptors copy these bytes from the GND-SIN descriptor. |

Custom descriptors are built from the GND-SIN record (`0x24EFAA`, ID 1) by
replacing the handler, ID, family, suffix, labels, defaults and format bytes.
That keeps the uninterpreted tail bytes consistent with a known-good audio
machine.

## ID table

`0x252092 + 4·ID` holds a pointer to the descriptor of machine `ID`
(192 entries). In stock OS 1.63:

- These IDs point to the empty descriptor `0x24EF54`: 0, 4–15, 29–31,
  40–47, 73–79, 86–95, 114–119, 124–127, 164 and 169–175.
- **26 more are free for custom machines** besides 4–15 (see
  [free IDs](#free-ids)): **30–31, 40–47, 73–79 and 86–94**. The kit's examples
  use 8 (GND-SW) and 15 (NFX-GN).

The OS resolves defaults, labels and the handler through this table when a
track is assigned, and stores the handler pointer in the per-track slot
`0x29F27C + 4·t`.

### Free IDs

| IDs | Status | Evidence |
| --- | --- | --- |
| 4–15 | Free; used by custom machines on hardware | The GND family's empty slots |
| 30–31, 40–47, 73–79, 86–94 | Free (verified in the emulator) | Static audit and differential test, below |
| 29 | **Not free** | Empty descriptor, but DSP type 30 runs a hidden stock routine (`0x102D06`, `0x102D31`, `0x102D49`) |
| 95 | **Not free** | The machine-page display treats non-ROM IDs above 94 as MIDI/controller machines |
| 114–119, 124–127, 164, 169–175 | **Not free** | Inside the MID/CTR/ROM/RAM ranges |
| 0 | **Not free** | "No machine" |

**What the OS tests.** The ColdFire code tells families apart only by these
tests on a track's ID:

| Test | Family |
| --- | --- |
| `id − 80 ≤ 5` (unsigned) | INP, 80–85 |
| `id & 0xF0 == 0x60` | MID, 96–111 |
| `id == 112`, `id == 113`, `id & 0xF8 == 0x78` | CTR |
| `id − 128 ≤ 31`, `id − 176 ≤ 15`, `id − 128 ≤ 40`, 160/165/166 | ROM and RAM |
| `id > 94` (not ROM): MIDI/controller page | Machine-page display (`0x2282EE`) |
| `id > 95`: non-synthesis branch | Assignment (`0x2052FE`) |

Nothing tests the TRX, EFM, E12 or P-I ranges, so an ID in a gap between
those families gets no family-specific handling.

**How this was established:**
- **Static audit.** Every ColdFire access to the current kit's machine IDs (122
  code sites) was checked for range tests.
- **Readers in use.** Reads of the ID array, the ID table and the handler
  slots were traced during boot, assignment, trigs, kit SysEx and the browser.
- **Differential test.** One image registered the same machine under every
  candidate ID and, as a control, under ID 15. The same scenario then ran
  once per ID:
  - SysEx assignment, knobs and trigs, with the audio recorded;
  - kit save/load (`0x59`/`0x58`) and a dump/receive round trip (`0x53`/`0x52`);
  - the front-panel browser, with LCD captures.
- **Result.** Audio and LCD were bit-identical to the control. Every RAM
  difference was stale task-stack data, the browser's row and scroll state,
  the menu-row byte of the inverse map, SysEx checksums, or DSP2's position
  in its block at the snapshot. The emulator is deterministic: two control
  runs matched byte for byte.

**Not covered yet:**
- sequencer playback of patterns with parameter locks;
- track copy/paste, sound save/recall, randomize, song mode;
- hardware.

Boot-test one image with a machine on a new ID before relying on it.

**DSP type.** For any ID, also check that DSP type ID+1 still points at the
fallback entries (`mdkit.machine.dsp_type_free`; `register_id(..., dsp)` checks
it).

**Removing a machine.** Point its ID-table entry back to `0x24EF54`, and point
its three DSP dispatch cells to the fallback entries. Old kits and SysEx
assignments then produce silence instead of running code over reclaimed
memory. Adding a machine never requires this. Removing the eight RAM machines
(IDs 160–163, 165–168) is a way to free sample memory for delay-line machines
(see [packing](12-packing-firmware.md#removing-the-ram-machines-to-gain-delay-memory)).

## Families and menus

`0x252396` holds the **family table**: 8-byte records, terminated by an all-zero
record.

```text
+0  4 bytes  name, NUL-terminated ("GND\0")
+4  u32 BE   pointer to a zero-terminated list of descriptor pointers
```

| Index | Family | Stock list |
| --- | --- | --- |
| 0 | GND | `0x251E3E`: `0x24EF54, 0x24EFAA, 0x24F000, 0x24F056, 0` (GND---, SIN, NS, IM) |
| 1–8 | TRX, EFM, E12, P-I, INP, MID, CTR, ROM | stock lists |
| 9 | RAM | `0x25206E` |
| — | terminator | `0x2523E6`: 8 zero bytes |

How the OS uses them:

- **At boot**, code at `0x22C1A0` walks every family list and builds the
  **inverse map** `0x28D838 + 2·ID = {family index, row}`. This is what makes
  reopening kit edit on a custom track select the right family and row. It is
  computed from the lists, so **never prefill it**.
- **In the browser**, code at `0x22C204` counts the selected list up to its zero
  terminator and caches the count at `0x28C2D8`. Lists can therefore grow without
  patching any count.
- The number of families comes from the table terminator, so the table can grow
  too, if it is relocated to where there is room.

### Adding a machine to an existing family

1. Build a new list in a flash bank: the original entries (copied verbatim),
   the new descriptor pointers, then 0.
2. Replace the family record's list pointer with the new list's CPU alias
   address. For GND that is the word at `0x25239A`, originally `0x251E3E`.

### Renaming or replacing a family

A build that gives up the RAM machines (to gain delay memory) can reuse
family 9's slot for its own family:

- replace the name bytes at `0x252396 + 9·8`, for example `RAM\0` → `NFX\0`;
- replace the list pointer at `0x25239A + 9·8` with the new menu;
- map the removed RAM IDs to the empty descriptor, as above.

To add a family while keeping every stock family, append it instead (next
section). The NFX-GN example does that.

### Adding a family

The stock table has no room after its terminator. Copy the whole table (80
bytes + the new records + a zero record) to a flash bank and repoint **all eight
references** to it:

| Reference (operand address) | Original value | New value |
| --- | --- | --- |
| `0x22C1A8` | `0x252396` (table) | new table |
| `0x23065A` | `0x252396` (table) | new table |
| `0x22C210`, `0x231A60`, `0x231ABA`, `0x231B10`, `0x231F22`, `0x235368` | `0x25239A` (table + 4) | new table + 4 |

The NFX-GN example adds NFX this way as an eleventh family; the name display
and menu code then show it after RAM. Verify the eight sites against your image
before patching: each must contain the original value
(see [packing firmware](12-packing-firmware.md#guarded-edits)).

## Control handler ABI

Each descriptor points at a ColdFire function that converts raw knob words into
the words DSP2 receives.

```c
/* C calling convention, arguments on the stack */
int handler(u32 *packet, const u16 *raw);
```

| Item | Meaning |
| --- | --- |
| `4(sp)` | `packet`: output buffer of 32-bit words. `packet[0]` is the type slot, filled by the OS. `packet[1..]` are your control words. |
| `8(sp)` | `raw`: 16-bit raw words, one per synthesis knob: `raw[0..7]` at byte offsets 0, 2, …, 14 |
| return `D0` | **Number of packet words, including `packet[0]`**. Stock GND-SIN and GND-SW return 5, NFX-GN returns 2. Counts up to 11 have been used. |
| Registers | Follow the C ABI: `D0/D1/A0/A1` scratch, preserve `D2–D7`, `A2–A6`. The existing handlers save and restore `D2`/`A2` when they use them. |
| Stack | Any depth the OS task stack allows. Helpers can be called with `bsr`/`jsr`. |

The low 24 bits of each `packet[i]` become DSP word `Y:S+i` of the track's state
block (`S = 0x800 + 0x40·t`). The upper 8 bits are ignored. The DSP words form
the **packet**; the words above it belong to the machine's DSP state.

Rules:

- **No state.** The OS calls the handler whenever it refreshes the packet: at
  least on assignment, while a knob slews and after tempo changes. Compute
  everything from `raw` and globals.
- **Knob changes do not trigger `update`.** Packet refreshes rewrite the control
  words; only a trig sets the pending type word that makes the DSP call
  `update`. (**Verified**: a custom envelope does not retrigger on knob moves.)
- **Precompute on the ColdFire.** Table lookups, divisions and curve shaping
  are much cheaper here than per block on DSP2. Send the DSP exactly the
  coefficients it needs in its native format (Q23, phase steps, sample counts…).
- **Code runs from flash.** Link it at its **CPU alias address**
  (`0x10000000 + flash offset`), and make every absolute reference use the alias
  too. That includes jump tables, `lea` of tables, and the handler pointer in the
  descriptor.
- The handler may call stock MainOS helpers and read stock tables by absolute
  address. GND-SW reuses the decay table at `0x24BF94`; a tempo-synced machine
  can read the tempo global `0x100150C`. Handlers in separately linked blobs can call each other through
  link-time symbol definitions.

### Recovered stock conversions

The GND handlers (**Verified**, exact on 18 probes). `u = raw` (≈ `value << 7`):

```text
GND-SIN (0x201130):  +1 pitch   = pitch[q & 127] << (q >> 7),  q = u >> 4   (pitch table 0x24BD94)
                     +2 decay   = decay[u >> 5]                                 (decay table 0x24BF94)
                     +3 ramp    = (u * u) >> 7
                     +4 rdecay  = decay[u >> 5]
GND-NS (0x201194):   +1 decay   = decay[u >> 5]
GND-IM (0x2011B4):   +1/+3 dur  = u >> 7 if u <= 8191 else 64 + ((u - 8192)^2 >> 14)
                     +2/+4 lvl  = (u * u) >> 5
```

Other stock handlers are listed per machine in the reference workspace's
machine atlas. Only the GND ones have been derived exactly.

## What happens on assignment

1. The front panel (or the `5B` SysEx) selects an ID for track `t`.
2. The OS stores the ID in the current kit (`0x7001AA + 4·t`), loads defaults and
   labels from the descriptor, and stores the handler in `0x29F27C + 4·t`.
3. The handler builds the packet, and the OS sends it to DSP2 `Y:S…`.
4. On the next trig the pending word `Y:S` receives the DSP type. DSP2 then calls
   `init` (type changed) and `update`, and `render` from then on
   (see [DSP2 voice ABI](08-dsp2-voice-abi.md)).
