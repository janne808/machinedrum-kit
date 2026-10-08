# Shared by the NFX-SV and NFX-4P examples

[NFX-SV](../../nfx-sv/README.md) and [NFX-4P](../../nfx-4p/README.md) have the
same eight knobs, the same AD envelope and VCA, and read the same tanh table in
DSP2 internal X. What they share lives here:

| File | Contents |
| --- | --- |
| `control.s` | `svf_control` and its helpers: the NFX-SV handler, which NFX-4P's `ladder_control` calls first |
| `generate_tables.py`, `tables.inc` | The handler's 65-entry tables (cutoff, damping, decay, depth, attack) and the tanh table |
| `nfx.py` | Bit-exact models of the control words, the block-rate envelope and the block VCA; the verification cases and the kernel and booted runs both `verify.py` scripts use |

**Packet words** `Y:S+1…+8`:
1. cutoff base;
2. damping (NFX-SV) or fb/8 (NFX-4P);
3. mode;
4. envelope depth (signed);
5. attack coefficient, where bit 23 means the mantissa is `α·2³¹`;
6. decay coefficient;
7. gain;
8. VCA: 0 bypass, 1 envelope, ≥ 2 the gate length in samples.

`svf_control` computes the gate from the OS 1.63 tempo word at `0x100150C`
(BPM × 24): `VCA + 1` 1/128 notes.

**Envelope.** A trig resets the level and starts the attack, which charges
toward twice full scale and ends exactly at full scale; the decay is exponential.
The envelope advances once per 32-sample block by its exact 32-sample step,
`e = 1 − (1 − k)³²` (five rounds of `e ← 2e − e²`), and with ENVA away from 64
the cutoff ramps linearly across each block.

**VCA.** One linear gain ramp per block toward the envelope level, or toward
full scale while the gate count lasts. The change is clamped to full scale per
1 ms; a block held at full scale skips the multiply.

**tanh table.** `tanh(8·m)`, m = 0…1 in 1,024 intervals: 1,025 words at
`X:0x280`, in the free internal X gap, uploaded with the DSP2 stream by
`mdkit.machine.add_x_table`. Internal reads cost no wait states.
