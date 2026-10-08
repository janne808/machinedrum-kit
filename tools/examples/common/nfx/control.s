| NFX-SV control handler (svf_control) and its helpers, shared by the NFX-SV and
| NFX-4P examples and linked at the CS0 alias of the example's flash bank
| (0x100FF200). It converts the eight raw knobs (about value << 7,
| slewed by the OS) into the machine's control words at packet +1..+8:
|   +1 cutoff base   +2 damping   +3 mode   +4 envelope depth
|   +5 attack (bit 23: the mantissa is alpha * 2^31)   +6 decay   +7 gain
|   +8 VCA: 0 bypass, 1 envelope, >= 2 gate length in samples (tempo-synced)
| Tables: tables.inc (generate_tables.py).
        .text
        .global svf_control,svf_attack_coef,svf_knob,svf_rate,svf_time,svf_hold,svf_lookup
svf_control:
        move.l %a2,-(%sp)
        move.l %d2,-(%sp)
        move.l 12(%sp),%a0
        move.l 16(%sp),%a1
        moveq #0,%d0
        move.w (%a1),%d0
        bsr svf_knob
        lea svf_cutoff,%a2
        bsr svf_lookup
        move.l %d0,4(%a0)
        moveq #0,%d0
        move.w 2(%a1),%d0
        bsr svf_knob
        lea svf_damping,%a2
        bsr svf_lookup
        move.l %d0,8(%a0)
| MODE sign bit chooses BP/HP; low 23 bits are interpolation fraction.
        moveq #0,%d0
        move.w 4(%a1),%d0
        bsr svf_knob
        cmp.l #64,%d0
        bge svf_mode_upper
        lsl.l #8,%d0
        lsl.l #8,%d0
        lsl.l #1,%d0
        bra svf_mode_ready
svf_mode_upper:
        cmp.l #127,%d0
        beq svf_mode_hp
        sub.l #64,%d0
        move.l #133152,%d1
        mulu.l %d1,%d0
        or.l #0x800000,%d0
        bra svf_mode_ready
svf_mode_hp:
        move.l #0xffffff,%d0
svf_mode_ready:
        move.l %d0,12(%a0)
| Bipolar envelope depth. 64 is neutral, 0 maximum downward sweep.
        moveq #0,%d0
        move.w 6(%a1),%d0
        bsr svf_knob
        lea svf_depth,%a2
        bsr svf_lookup
        move.l %d0,16(%a0)
| ATK: charge toward 2x full scale (svf_attack_coef).
| One word: bits 0-22 mantissa, bit 23 set for the 2^-31 scale (DSP shift 8).
        moveq #0,%d0
        move.w 8(%a1),%d0
        bsr svf_attack_coef
        tst.l %d1
        beq.s svf_attack_packed
        bset #23,%d0
svf_attack_packed:
        move.l %d0,20(%a0)
| VCA: 127 bypass (0); 64-126 envelope-controlled output amplitude (1);
| 0-63 gate open for svf_hold(k) samples (tempo-synced, >= 2).
        moveq #0,%d0
        move.w 14(%a1),%d0
        bsr svf_knob
        moveq #0,%d1
        moveq #127,%d2                | moveq/cmp: 2 bytes shorter than cmp.l #imm
        cmp.l %d2,%d0
        beq.s svf_vca_word
        moveq #1,%d1
        moveq #64,%d2
        cmp.l %d2,%d0
        bge.s svf_vca_word
        bsr svf_hold
        moveq #2,%d1
        cmp.l %d1,%d0
        blt.s svf_vca_word
        move.l %d0,%d1
svf_vca_word:
        move.l %d1,32(%a0)
| DEC: exponential discharge coefficients.
        lea svf_time,%a2
        moveq #0,%d0
        move.w 10(%a1),%d0
        bsr svf_rate
        move.l %d0,24(%a0)
| GAIN: k/64 in physical audio units; DSP coefficient folds in input /32.
        moveq #0,%d0
        move.w 12(%a1),%d0
        bsr svf_knob
        lsl.l #8,%d0
        lsl.l #4,%d0
        move.l %d0,28(%a0)
        moveq #9,%d0
        move.l (%sp)+,%d2
        move.l (%sp)+,%a2
        rts
| ATK word in D0 -> 24-bit mantissa in D0, DSP right shift (0 or 8) in D1.
| Table holds alpha*2^31; small coefficients keep 2^-31 resolution via shift 8.
| svf_attack is in tables.inc.
svf_attack_coef:
        lea svf_attack,%a2
        bsr svf_knob
        tst.l %d0
        bne svf_attack_table
        move.l #0x7fffff,%d0
        moveq #0,%d1
        rts
svf_attack_table:
        bsr svf_lookup
        moveq #0,%d1
        cmp.l #0x800000,%d0
        blt svf_attack_fine
        lsr.l #8,%d0
        rts
svf_attack_fine:
        moveq #8,%d1
        rts
| HOLD knob k in D0 -> gate samples in D0 (D1 clobbered): k+1 1/128 notes, as the
| master delay TIME steps: U = (k+1)*128 (7 fraction bits); OS 1.63 0x20b4b4:
| samples = ((U * 44100) >> 10) * 360 / tempo, tempo = BPM*24 live at 0x100150c.
| 0 -> 1/128 note, 63 -> 1/2.
svf_hold:
        addq.l #1,%d0
        lsl.l #7,%d0
        mulu.w #44100,%d0
        moveq #10,%d1
        lsr.l %d1,%d0
        move.l #360,%d1
        mulu.l %d1,%d0
        move.l 0x100150c,%d1
        divs.l %d1,%d0
        rts
svf_knob:
        add.l #64,%d0
        lsr.l #7,%d0
        cmp.l #127,%d0
        ble svf_knob_done
        moveq #127,%d0
svf_knob_done:
        rts
svf_rate:
        bsr svf_knob
        tst.l %d0
        bne svf_lookup
        move.l #0x7fffff,%d0
        rts
svf_lookup:
        cmp.l #127,%d0
        bne svf_lookup_normal
        move.l 256(%a2),%d0
        rts
svf_lookup_normal:
        move.l %d0,%d1
        and.l #1,%d1
        lsr.l #1,%d0
        move.l (%a2,%d0.l*4),%d2
        tst.l %d1
        beq svf_lookup_ready
        addq.l #1,%d0
        add.l (%a2,%d0.l*4),%d2
        asr.l #1,%d2
svf_lookup_ready:
        move.l %d2,%d0
        rts
        .balign 4
        .include "tables.inc"
