| NFX-4P control handler, linked after ../common/nfx/control.s at the CS0 alias
| of the example's flash bank (0x100FF200).
        .text
        .global ladder_control
| NFX-4P packet: NFX-SV's (cutoff, mode, envelope, VCA incl. the tempo gate)
| from svf_control, then the ladder's own RESO and GAIN words (uLADR):
| +2 = fb/8 with fb = 5 reso, reso = k/127; +7 = gain^4/4, gain = k/127.
ladder_control:
        move.l 8(%sp),-(%sp)          | raw knobs
        move.l 8(%sp),-(%sp)          | packet
        jsr svf_control
        addq.l #8,%sp
        move.l %a2,-(%sp)
        move.l %d2,-(%sp)
        move.l 12(%sp),%a0
        move.l 16(%sp),%a1
        moveq #0,%d0
        move.w 2(%a1),%d0
        jsr svf_knob
        move.l #5242880,%d1           | 5/8 of full scale
        mulu.l %d1,%d0
        moveq #63,%d1
        add.l %d1,%d0
        moveq #127,%d1
        divu.l %d1,%d0
        move.l %d0,8(%a0)
        moveq #0,%d0
        move.w 12(%a1),%d0
        jsr svf_knob
        lea ladder_gain(%pc),%a2
        jsr svf_lookup
        move.l %d0,28(%a0)
        moveq #9,%d0
        move.l (%sp)+,%d2
        move.l (%sp)+,%a2
        rts
        .balign 4
ladder_gain:
        .include "gain.inc"
