| Sample-loader guards for a reservation of DSP2 memory from GUARD_CEILING up
| (docs/12-packing-firmware.md#reserving-sample-memory). mdkit.machine.
| reserve_sample_memory() replaces two MainOS loader sites with calls to these
| routines; each re-executes the instructions it displaced and rejects a sample
| whose end (+0x100 words margin) would reach the reserved region, by jumping
| to the loader's own error path. Assemble with --defsym GUARD_CEILING=0x...
        .text
        .global guard_loader_a,guard_loader_b

| Loader path A, called from MainOS 0x20D2A8 (was: move.l %d1,48(%sp); moveq #31,%d0).
guard_loader_a:
        move.l  %d1,52(%sp)         | displaced store (sp is 4 lower inside the call)
        move.l  %d1,%d0
        ble     reject_a
        addq.l  #1,%d0
        lsr.l   #1,%d0              | sample length in words
        add.l   68(%sp),%d0         | + current word offset
        add.l   0x29f38e,%d0        | + DSP sample base
        add.l   #0x100,%d0          | + margin
        cmp.l   #GUARD_CEILING,%d0
        bhi     reject_a
        moveq   #31,%d0             | displaced instruction
        rts
reject_a:
        addq.l  #4,%sp              | drop the return address
        jmp     0x20d51e            | the loader's own error path (clears the slot)

| Loader path B, called from MainOS 0x20D7E2 (was: move.l %d1,-28(%fp); ble.w ...).
guard_loader_b:
        move.l  %d1,-28(%fp)        | displaced store
        move.l  %d1,%d0
        ble     reject_b
        addq.l  #1,%d0
        lsr.l   #1,%d0
        add.l   12(%fp),%d0
        add.l   0x29f38e,%d0
        add.l   #0x100,%d0
        cmp.l   #GUARD_CEILING,%d0
        bhi     reject_b
        tst.l   %d1
        rts
reject_b:
        addq.l  #4,%sp
        jmp     0x20da4a
