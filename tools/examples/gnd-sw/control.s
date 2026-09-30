| GND-SW control handler. Converts the four GND knobs:
|   PTCH -> +1 phase step for the nearest MIDI note (pitch.inc)
|   DEC  -> +2 stock decay coefficient   (stock table 0x24BF94, as GND-SIN)
|   RAMP -> +3 (raw * raw) >> 7          (as GND-SIN)
|   RDEC -> +4 stock decay coefficient   (as GND-SIN)
| int saw_control(u32 *packet, const u16 *raw); returns 5 (type + 4 words).
        .text
        .global saw_control
saw_control:
        move.l %a2,-(%sp)
        move.l 8(%sp),%a0           | packet (4 bytes further up after the push)
        move.l 12(%sp),%a1          | raw knob words
| PTCH is an integer MIDI note: round the slewed raw value to the nearest note.
        moveq #0,%d0
        move.w (%a1),%d0
        add.l #64,%d0
        lsr.l #7,%d0
        cmp.l #127,%d0
        ble saw_note_ready
        moveq #127,%d0
saw_note_ready:
        lea saw_pitch,%a2
        move.l (%a2,%d0.l*4),%d0
        move.l %d0,4(%a0)
        moveq #0,%d0
        move.w 2(%a1),%d0
        lsr.l #5,%d0
        lea 0x24bf94,%a2            | stock decay table
        move.l (%a2,%d0.l*4),%d0
        move.l %d0,8(%a0)
        moveq #0,%d0
        move.w 4(%a1),%d0
        mulu.l %d0,%d0
        lsr.l #7,%d0
        move.l %d0,12(%a0)
        moveq #0,%d0
        move.w 6(%a1),%d0
        lsr.l #5,%d0
        move.l (%a2,%d0.l*4),%d0
        move.l %d0,16(%a0)
        moveq #5,%d0
        move.l (%sp)+,%a2
        rts
        .balign 4
saw_pitch:
        .include "pitch.inc"
