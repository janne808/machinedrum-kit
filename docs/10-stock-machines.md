# Stock machines

OS 1.63 has **135 machine descriptors** in ten families. They use 151 distinct
DSP2 entry points, all resident code: DSP2 selects implementations through the
dispatch tables and never loads code on assignment. The table at the end lists
every descriptor with its IDs, handler, DSP entries and knob labels/defaults.

## Families

| Family | IDs | Descriptors | Implementation |
| --- | --- | ---: | --- |
| GND | 0–3 | 4 | Empty fallback, sine (SIN), noise (NS), impulse (IM). Their conversions are fully derived (see [catalogue](07-machine-catalogue.md#recovered-stock-conversions)). |
| TRX | 16–29 | 14 | Separate synthesis paths per machine; some share source/coefficient tables (CH/OH/CY share six metallic-source bases at `0x102600`). |
| EFM | 32–39 | 8 | Coupled phase-modulation oscillators with table lookups; heavy use of low X/L scratch. |
| E12 | 48–63 | 16 | Built-in 12-bit samples (two per word) through a 21-entry descriptor table at `0x103D7B`; shared playback renderers (`0x103838`, `0x103945`, `0x103A10`, `0x103B46`). |
| P-I | 64–72 | 9 | Physical-model resonators with per-voice external delay buffers from `0x135600`; MA uses stochastic excitation. |
| INP | 80–85 | 6 | External input: three A/B pairs sharing update/render bodies. |
| MID | 96–111 | 16 | **MIDI output** machines: no DSP synthesis; they use the fallback DSP entries. |
| CTR | 112–113, 120–123 | 6 | Parameter controllers; fallback DSP entries. |
| ROM | 128–159, 176–191 | 48 | One resident UW sample player (`0x13D / 0x15A / 0x16C`); slot-specific metadata at `0x147E00`. |
| RAM | 160–163, 165–168 | 8 | Four recorders (shared renderer `0x103579`) and four players (the ROM player through fixed-index wrappers). |

## Algorithms worth knowing

**GND-SIN** (type 2): the reference carrier used by the first custom-machine
experiments.

- Init is RTS.
- Update resets phase `+7/+8` and the envelopes (`+6 = 0x7FFFF8`, `+9/+A =
  0:0x7FFFF8`).
- Render adds the pitch ramp to the base pitch, then produces a 32-sample block:
  - the 15-bit phase index looks up `X:0x148000`;
  - the sample is shifted right by 2;
  - a two-word amplitude envelope multiplies it.
- With RAMP = 0 the frequency is `44100 · pitch_word / (64 · 32768)`.

**GND-IM**: two counted segments at two levels, then zero:

```text
for 32 samples:
    if first_remaining:    first_remaining -= 1;  out(first_level)
    elif second_remaining: second_remaining -= 1; out(second_level)
    else: out(0)
```

**Fallback renderer** (`0x10008F`): writes 32 zeros inside a deliberately padded
loop (`DO #50` of two NOPs per sample, at `P:0x100093`). It costs **3,438 cycles**:
an idle track is not free. Every custom machine is cheaper when idle. The pad
can be shortened (see [packing](12-packing-firmware.md#optional-dsp2-edits)),
but its purpose is unknown.

**E12 samples** are packed two 12-bit samples per word. The reader halves the
position and extracts halves with 12-bit shifts. The sample sections run
`0x103DBA–0x135205` in 21 abutting pairs (sample + a `0x99`-word tail).
Reclaiming them for other uses means disabling all 16 E12 dispatch triples first.

**Sample players** (ROM/RAM) read four-word records at `0x147E00 + 4·slot`:
sample base, length, and two control words that the recorder updates as it
records.

## Measured costs

Mean render cost per 32-sample block (emulator, default patches, including
interrupts that land inside the call):

| Machine | Cycles |
| --- | ---: |
| GND--- (idle track) | 3,438 |
| GND-SIN | 695 |
| TRX-BD / TRX-SD / TRX-CY | 2,883 / 2,057 / 2,843 (max 3,115) |
| EFM-BD / EFM-CY | 2,205 / 3,000 (max 3,198) |
| E12-SD | 2,030 |
| P-I-BD / P-I-SD | 2,854 / 3,115 |

The emulator charges no memory wait states, and the stock machines run from
external memory, so hardware costs are higher.

## Catalogue

Columns:

- **SysEx model / UW** are the bytes used in the assignment SysEx (`5B`).
- **Handler** is the ColdFire control handler.
- **Init / Update / Render** are the DSP2 entry addresses.

Knob labels and defaults are the descriptor's.

| Machine | ID | SysEx model / UW | DSP type | Handler | Init / Update / Render (P) | Knobs (default) |
| --- | ---: | --- | ---: | --- | --- | --- |
| GND--- | 0 | 0 / 0 | 1 | `201128` | `10008E` / `10008E` / `10008F` | — |
| GND-SIN | 1 | 1 / 0 | 2 | `201130` | `1000A3` / `1000A4` / `1000AD` | PTCH 73, DEC 96, RAMP 0, RDEC 0 |
| GND-NS | 2 | 2 / 0 | 3 | `201194` | `1000DD` / `1000E4` / `1000EA` | DEC 64 |
| GND-IM | 3 | 3 / 0 | 4 | `2011B4` | `100109` / `10010A` / `100113` | UP 64, UVAL 127, DOWN 0, DVAL 127 |
| TRX-BD | 16 | 16 / 0 | 17 | `20122A` | `1003DA` / `1003E1` / `100408` | PTCH 64, DEC 64, RAMP 0, RDEC 0, STRT 0, NOIS 0, HARM 0, CLIP 0 |
| TRX-SD | 17 | 17 / 0 | 18 | `2013A6` | `10074C` / `10075B` / `100776` | PTCH 64, DEC 32, BUMP 0, BENV 64, SNAP 64, TONE 64, TUNE 64, CLIP 0 |
| TRX-XT | 18 | 18 / 0 | 19 | `2014C2` | `101882` / `101883` / `101891` | PTCH 64, DEC 48, RAMP 48, RDEC 102, DAMP 64, DIST 0, DTYP 0 |
| TRX-CP | 19 | 19 / 0 | 20 | `20158C` | `10194F` / `101966` / `101972` | CLPY 64, TONE 64, HARD 127, RICH 64, RATE 64, ROOM 32, RSIZ 32, RTUN 64 |
| TRX-RS | 20 | 20 / 0 | 21 | `2016B8` | `101AB0` / `101AB8` / `101ACD` | PTCH 64, DEC 32, DIST 0 |
| TRX-CB | 21 | 21 / 0 | 22 | `201714` | `101EA0` / `101EBB` / `101EC4` | PTCH 64, DEC 64, ENH 64, DAMP 0, TONE 64, BUMP 64 |
| TRX-CH | 22 | 22 / 0 | 23 | `201800` | `10201F` / `102034` / `10204D` | GAP 32, DEC 32, HPF 64, LPF 64, MTAL 64 |
| TRX-OH | 23 | 23 / 0 | 24 | `2018B2` | `1021F4` / `102209` / `102222` | GAP 64, DEC 64, HPF 64, LPF 64, MTAL 64 |
| TRX-CY | 24 | 24 / 0 | 25 | `201976` | `1023C9` / `1023EB` / `1023FA` | RICH 64, DEC 64, TOP 64, TTUN 64, SIZE 64, PEAK 64 |
| TRX-MA | 25 | 25 / 0 | 26 | `201BC0` | `1028F4` / `102914` / `10291F` | ATT 32, SUS 32, REV 0, DAMP 0, RATL 64, RTYP 64, TONE 64, HARD 64 |
| TRX-CL | 26 | 26 / 0 | 27 | `201CB0` | `102AA9` / `102AB3` / `102ABF` | PTCH 64, DEC 32, DUAL 64, ENH 0, TUNE 64, CLIC 0 |
| TRX-XC | 27 | 27 / 0 | 28 | `201D80` | `102B87` / `102B88` / `102B96` | PTCH 64, DEC 32, RAMP 32, RDEC 96, DAMP 0, DIST 0, DTYP 0 |
| TRX-B2 | 28 | 28 / 0 | 29 | `201FD2` | `102C31` / `102C4A` / `102C5A` | PTCH 0, DEC 64, RAMP 96, HOLD 64, TICK 0, NOIS 0, DIRT 0, DIST 0 |
| TRX-S2 | 29 | 29 / 0 | 30 | `201E46` | `102D06` / `102D31` / `102D49` | PTCH 64, DEC 32, NOIS 32, NDEC 96, POWR 0, TUNE 0, NTUN 0, NTYP 0 |
| EFM-BD | 32 | 32 / 0 | 33 | `20211C` | `102EEF` / `102EF3` / `102F06` | PTCH 36, DEC 76, RAMP 64, RDEC 55, MOD 64, MFRQ 64, MDEC 32, MFB 127 |
| EFM-SD | 33 | 33 / 0 | 34 | `2021FC` | `102F68` / `102F6F` / `102F85` | PTCH 64, DEC 55, NOIS 64, NDEC 64, MOD 64, MFRQ 32, MDEC 44, HPF 16 |
| EFM-XT | 34 | 34 / 0 | 35 | `2022F4` | `10304E` / `103052` / `103065` | PTCH 64, DEC 64, RAMP 38, RDEC 87, MOD 48, MFRQ 84, MDEC 55, CLIC 127 |
| EFM-CP | 35 | 35 / 0 | 36 | `2023DE` | `1030EC` / `1030F0` / `103104` | PTCH 83, DEC 41, CLPS 64, CDEC 36, MOD 32, MFRQ 64, MDEC 127, HPF 32 |
| EFM-RS | 36 | 36 / 0 | 37 | `2024D0` | `1031A3` / `1031A8` / `1031BB` | PTCH 64, DEC 32, MOD 64, HPF 32, SNAR 64, SPTC 64, SDEC 44, SMOD 64 |
| EFM-CB | 37 | 37 / 0 | 38 | `2025E6` | `103272` / `103277` / `10328C` | PTCH 64, DEC 64, SNAP 64, FB 96, MOD 64, MFRQ 64, MDEC 64 |
| EFM-HH | 38 | 38 / 0 | 39 | `2026F8` | `10332E` / `103332` / `103349` | PTCH 96, DEC 32, TREM 0, TFRQ 0, MOD 64, MFRQ 64, MDEC 64, FB 127 |
| EFM-CY | 39 | 39 / 0 | 40 | `20280C` | `1033F1` / `1033F4` / `10340F` | PTCH 64, DEC 72, FB 96, HPF 127, MOD 64, MFRQ 32, MDEC 64 |
| E12-BD | 48 | 48 / 0 | 49 | `202948` | `1036B6` / `103819` / `103945` | PTCH 64, DEC 64, SNAP 64, SPLN 64, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-SD | 49 | 49 / 0 | 50 | `202AE8` | `103718` / `103783` / `103B46` | PTCH 64, DEC 80, HP 0, RING 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-HT | 50 | 50 / 0 | 51 | `203434` | `1036C4` / `103768` / `103838` | PTCH 64, DEC 86, HP 0, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-LT | 51 | 51 / 0 | 52 | `203434` | `1036D2` / `103768` / `103838` | PTCH 64, DEC 82, HP 0, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-CP | 52 | 52 / 0 | 53 | `203434` | `1036FC` / `103768` / `103838` | PTCH 64, DEC 127, HP 0, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-RS | 53 | 53 / 0 | 54 | `20325E` | `103718` / `10379C` / `103A10` | PTCH 64, DEC 64, HP 0, RATL 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-CB | 54 | 54 / 0 | 55 | `203434` | `10373E` / `103768` / `103838` | PTCH 64, DEC 64, HP 0, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-CH | 55 | 55 / 0 | 56 | `203434` | `1036E0` / `103768` / `103838` | PTCH 64, DEC 80, HP 48, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-OH | 56 | 56 / 0 | 57 | `20399E` | `1036EE` / `103768` / `103838` | PTCH 64, DEC 127, HP 32, STOP 127, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-RC | 57 | 57 / 0 | 58 | `202CB8` | `103718` / `1037B5` / `103B46` | PTCH 64, DEC 96, HP 0, BELL 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-CC | 58 | 58 / 0 | 59 | `202E9E` | `103722` / `103768` / `103838` | PTCH 64, DEC 96, HP 0, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-BR | 59 | 59 / 0 | 60 | `202AE8` | `103718` / `103800` / `103B46` | PTCH 64, DEC 96, HP 0, REAL 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-TA | 60 | 60 / 0 | 61 | `2037D6` | `103730` / `103768` / `103838` | PTCH 64, DEC 96, HP 0, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-TR | 61 | 61 / 0 | 62 | `2035FA` | `10370A` / `103768` / `103838` | PTCH 64, DEC 76, HP 0, HPQ 0, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-SH | 62 | 62 / 0 | 63 | `20308C` | `103718` / `1037CE` / `103B46` | PTCH 64, DEC 127, HP 0, SLEW 127, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| E12-BC | 63 | 63 / 0 | 64 | `202AE8` | `103718` / `1037E7` / `103B46` | PTCH 64, DEC 64, HP 0, BC 127, STRT 0, RTRG 0, RTIM 64, BEND 64 |
| P-I-BD | 64 | 64 / 0 | 65 | `203B7A` | `142100` / `14213C` / `142145` | PTCH 80, DEC 26, HARD 127, HAMR 96, TENS 29, DAMP 122 |
| P-I-SD | 65 | 65 / 0 | 66 | `203C26` | `142D49` / `142D88` / `142D95` | PTCH 64, DEC 64, HARD 64, RING 16, TENS 0, RVOL 127, RDEC 96 |
| P-I-MT | 66 | 66 / 0 | 67 | `203CE4` | `1434A6` / `1434D6` / `1434DF` | PTCH 22, DEC 24, HARD 127, HAMR 127, TUNE 127, DAMP 96, SIZE 96, POS 64 |
| P-I-ML | 67 | 67 / 0 | 68 | `203DC8` | `143E0D` / `143E3D` / `143E49` | PTCH 64, DEC 64, HARD 127, TENS 0 |
| P-I-MA | 68 | 68 / 0 | 69 | `203E88` | `145AA7` / `145AB7` / `145ABB` | GRNS 99, DEC 0, GLEN 72, SIZE 96, HARD 64 |
| P-I-RS | 69 | 69 / 0 | 70 | `203F34` | `144AB9` / `144AF4` / `144B01` | PTCH 96, DEC 48, HARD 64, RING 64, RVOL 64, RDEC 64 |
| P-I-RC | 70 | 70 / 0 | 71 | `203FD2` | `145624` / `14565B` / `14566C` | PTCH 64, DEC 70, HARD 96, RING 32, AG 127, AU 127, BR 64, GRAB 127 |
| P-I-CC | 71 | 71 / 0 | 72 | `2040F8` | `144FEC` / `145023` / `145037` | PTCH 64, DEC 70, HARD 96, RING 32, AG 127, AU 127, BR 64, GRAB 127 |
| P-I-HH | 72 | 72 / 0 | 73 | `204216` | `14584F` / `145887` / `1458A0` | PTCH 64, DEC 70, CLSN 96, RING 32, AG 127, AU 127, BR 64, CLOS 127 |
| INP-GA | 80 | 80 / 0 | 81 | `204330` | `100130` / `10015E` / `10015F` | VOL 32, GATE 0, ATCK 32, HLD 1, DEC 64 |
| INP-GB | 81 | 81 / 0 | 82 | `204398` | `100147` / `10015E` / `10015F` | VOL 32, GATE 0, ATCK 32, HLD 1, DEC 64 |
| INP-FA | 82 | 82 / 0 | 83 | `204400` | `100130` / `1002D2` / `1002D3` | ALEV 32, GATE 0, FATK 0, FHLD 0, FDEC 0, FDPH 64, FFRQ 127, FQ 0 |
| INP-FB | 83 | 83 / 0 | 84 | `20449E` | `100147` / `1002D2` / `1002D3` | ALEV 32, GATE 0, FATK 0, FHLD 0, FDEC 0, FDPH 64, FFRQ 127, FQ 0 |
| INP-EA | 84 | 84 / 0 | 85 | `2045DC` | `100130` / `1001CD` / `1001D2` | ALEV 32, AHLD 0, ADEC 32, FQ 0, FDPH 64, FHLD 0, FDEC 32, FFRQ 127 |
| INP-EB | 85 | 85 / 0 | 86 | `20453C` | `100147` / `1001CD` / `1001D2` | ALEV 32, AHLD 0, ADEC 32, FQ 0, FDPH 64, FHLD 0, FDEC 32, FFRQ 127 |
| MID-01 | 96 | 96 / 0 | 97 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-02 | 97 | 97 / 0 | 98 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-03 | 98 | 98 / 0 | 99 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-04 | 99 | 99 / 0 | 100 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-05 | 100 | 100 / 0 | 101 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-06 | 101 | 101 / 0 | 102 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-07 | 102 | 102 / 0 | 103 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-08 | 103 | 103 / 0 | 104 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-09 | 104 | 104 / 0 | 105 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-10 | 105 | 105 / 0 | 106 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-11 | 106 | 106 / 0 | 107 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-12 | 107 | 107 / 0 | 108 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-13 | 108 | 108 / 0 | 109 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-14 | 109 | 109 / 0 | 110 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-15 | 110 | 110 / 0 | 111 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| MID-16 | 111 | 111 / 0 | 112 | `20112C` | `10008E` / `10008E` / `10008F` | NOTE 64, N2 64, N3 64, LEN 4, VEL 100, PB 64, MW 0, AT 0 |
| CTR-AL | 112 | 112 / 0 | 113 | `20112C` | `10008E` / `10008E` / `10008F` | SYN1 64, SYN2 64, SYN3 64, SYN4 64, SYN5 64, SYN6 64, SYN7 64, SYN8 64 |
| CTR-8P | 113 | 113 / 0 | 114 | `20112C` | `10008E` / `10008E` / `10008F` | P1 64, P2 64, P3 64, P4 64, P5 64, P6 64, P7 64, P8 64 |
| CTR-RE | 120 | 120 / 0 | 121 | `20112C` | `10008E` / `10008E` / `10008F` | TIME 64, MOD 64, MFRQ 64, FB 64, FLTF 64, FLTW 64, MONO 64, LEV 64 |
| CTR-GB | 121 | 121 / 0 | 122 | `20112C` | `10008E` / `10008E` / `10008F` | DVOL 64, PRED 64, DEC 64, DAMP 64, HP 64, LP 64, GATE 64, LEV 64 |
| CTR-EQ | 122 | 122 / 0 | 123 | `20112C` | `10008E` / `10008E` / `10008F` | LF 64, LG 64, HF 64, HG 64, PF 64, PG 64, PQ 64, GAIN 64 |
| CTR-DX | 123 | 123 / 0 | 124 | `20112C` | `10008E` / `10008E` / `10008F` | ATCK 64, REL 64, TRHD 64, RTIO 64, KNEE 64, HP 64, OUTG 64, MIX 64 |
| ROM-01 | 128 | 0 / 1 | 129 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-02 | 129 | 1 / 1 | 130 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-03 | 130 | 2 / 1 | 131 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-04 | 131 | 3 / 1 | 132 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-05 | 132 | 4 / 1 | 133 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-06 | 133 | 5 / 1 | 134 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-07 | 134 | 6 / 1 | 135 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-08 | 135 | 7 / 1 | 136 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-09 | 136 | 8 / 1 | 137 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-10 | 137 | 9 / 1 | 138 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-11 | 138 | 10 / 1 | 139 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-12 | 139 | 11 / 1 | 140 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-13 | 140 | 12 / 1 | 141 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-14 | 141 | 13 / 1 | 142 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-15 | 142 | 14 / 1 | 143 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-16 | 143 | 15 / 1 | 144 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-17 | 144 | 16 / 1 | 145 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-18 | 145 | 17 / 1 | 146 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-19 | 146 | 18 / 1 | 147 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-20 | 147 | 19 / 1 | 148 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-21 | 148 | 20 / 1 | 149 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-22 | 149 | 21 / 1 | 150 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-23 | 150 | 22 / 1 | 151 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-24 | 151 | 23 / 1 | 152 | `20467C` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-25 | 152 | 24 / 1 | 153 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-26 | 153 | 25 / 1 | 154 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-27 | 154 | 26 / 1 | 155 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-28 | 155 | 27 / 1 | 156 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-29 | 156 | 28 / 1 | 157 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-30 | 157 | 29 / 1 | 158 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-31 | 158 | 30 / 1 | 159 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-32 | 159 | 31 / 1 | 160 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| RAM-R1 | 160 | 32 / 1 | 161 | `204BA2` | `10351D` / `103537` / `103579` | MLEV 0, MBAL 64, ILEV 64, IBAL 64, CUE1 0, CUE2 0, LEN 127, RATE 127 |
| RAM-R2 | 161 | 33 / 1 | 162 | `204BA2` | `103528` / `10353A` / `103579` | MLEV 0, MBAL 64, ILEV 64, IBAL 64, CUE1 0, CUE2 0, LEN 127, RATE 127 |
| RAM-P1 | 162 | 34 / 1 | 163 | `20490E` | `000137` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| RAM-P2 | 163 | 35 / 1 | 164 | `20490E` | `00013A` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| RAM-R3 | 165 | 37 / 1 | 166 | `204BA2` | `10352D` / `10353D` / `103579` | MLEV 0, MBAL 64, ILEV 64, IBAL 64, CUE1 0, CUE2 0, LEN 127, RATE 127 |
| RAM-R4 | 166 | 38 / 1 | 167 | `204BA2` | `103532` / `103540` / `103579` | MLEV 0, MBAL 64, ILEV 64, IBAL 64, CUE1 0, CUE2 0, LEN 127, RATE 127 |
| RAM-P3 | 167 | 39 / 1 | 168 | `20490E` | `10009B` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| RAM-P4 | 168 | 40 / 1 | 169 | `20490E` | `10009F` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-33 | 176 | 48 / 1 | 177 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-34 | 177 | 49 / 1 | 178 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-35 | 178 | 50 / 1 | 179 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-36 | 179 | 51 / 1 | 180 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-37 | 180 | 52 / 1 | 181 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-38 | 181 | 53 / 1 | 182 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-39 | 182 | 54 / 1 | 183 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-40 | 183 | 55 / 1 | 184 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-41 | 184 | 56 / 1 | 185 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-42 | 185 | 57 / 1 | 186 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-43 | 186 | 58 / 1 | 187 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-44 | 187 | 59 / 1 | 188 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-45 | 188 | 60 / 1 | 189 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-46 | 189 | 61 / 1 | 190 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-47 | 190 | 62 / 1 | 191 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
| ROM-48 | 191 | 63 / 1 | 192 | `20490E` | `00013D` / `00015A` / `00016C` | PTCH 64, DEC 64, HOLD 127, BRR 0, STRT 0, END 127, RTRG 0, RTIM 64 |
