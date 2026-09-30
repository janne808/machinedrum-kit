; NFX-GN: neighbour gain. Reads the previous track's raw voice, scales it by
; GAIN (0 .. ~1.98x, unity at knob 64) and outputs it. Track 0 outputs silence.
; Packet: Y:S+1 = gain/2 in Q23 (0x400000 = unity). No private state.
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
