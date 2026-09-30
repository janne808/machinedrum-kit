| NFX-GN control handler. Linked at the CS0 alias address of its flash
| location (0x100FF200), together with ../common/guards.s.
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
