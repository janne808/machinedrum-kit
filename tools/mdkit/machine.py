"""Machine registration helpers for OS 1.63 MainOS and DSP2 payloads.

Every edit is compare-before-write. Addresses are MainOS CPU addresses (byte)
or DSP2 word addresses. See docs/07-machine-catalogue.md and
docs/12-packing-firmware.md.
"""
import re

from .image import DspStream, ImageError, MAIN_BASE, mainos_read, mainos_replace, mainos_word

# ---- MainOS structures -----------------------------------------------------------
DESCRIPTOR_TABLE = 0x24EF54        # 135 x 86 bytes; the first is the empty machine
EMPTY_DESCRIPTOR = 0x24EF54
GND_SIN_DESCRIPTOR = 0x24EFAA      # template for custom descriptors
DESCRIPTOR_SIZE = 86
ID_TABLE = 0x252092                # 192 x u32 descriptor pointers
# IDs that point at the empty descriptor in stock OS 1.63 and were verified free for
# custom synthesis machines: 4-15 (the GND slots, used on hardware) and the gaps
# between families that passed the static audit and the emulator differential test
# (docs/07-machine-catalogue.md#free-ids). Not free: 29 (DSP type 30 runs a hidden
# stock routine), 95 (drawn as a MIDI/controller machine page), 96+ (MID, CTR,
# ROM/RAM semantics).
GND_FREE_IDS = tuple(range(4, 16))
EXTRA_FREE_IDS = (30, 31) + tuple(range(40, 48)) + tuple(range(73, 80)) + tuple(range(86, 95))
FREE_IDS = GND_FREE_IDS + EXTRA_FREE_IDS
FAMILY_TABLE = 0x252396            # 8-byte records {name[4], list pointer}, zero record ends
STOCK_FAMILIES = 10
GND_LIST = 0x251E3E
# Operands that hold the family table address (+0) or its first list pointer (+4).
FAMILY_TABLE_REFS = (0x22C1A8, 0x23065A)
FAMILY_LIST_REFS = (0x22C210, 0x231A60, 0x231ABA, 0x231B10, 0x231F22, 0x235368)
RAM_FAMILY_INDEX = 9
RAM_IDS = (160, 161, 162, 163, 165, 166, 167, 168)
E12_FAMILY_INDEX = 3
E12_IDS = tuple(range(48, 64))

# ---- DSP2 --------------------------------------------------------------------------
DISPATCH_TABLES = (0x145AF5, 0x145BB6, 0x145C77)    # init, update, render; 193 entries
ENTRY_SYMBOLS = ('machine_init', 'machine_update', 'machine_render')

# ---- Sample-memory reservation --------------------------------------------------
# DSP2 sample memory (ROM slots, then RAM slots) runs up to base + total/2 words.
# Startup code sets per model branch: ROM budget, total budget (12-bit sample
# units, two per word) and the DSP base. Reserving the top of that memory for
# custom code or delay pools lowers both budgets by the same amount (keeping the
# stock RAM minimum, total - ROM) and guards both ROM-loader paths.
BUDGET_SITES = {
    # branch: (ROM budget site, form, stock ROM, total site, stock total, DSP base)
    '48-ROM': (0x200376, 'moveq-swap', 0x140000, 0x200382, 0x15F400, 0x150000),
    '32-ROM': (0x2003AA, 'long', 0x0F05F0, 0x2003B6, 0x0FFFF0, 0x180000),
}
GUARD_A_SITE, GUARD_A_OLD = 0x20D2A8, bytes.fromhex('2f410030701f')
GUARD_B_SITE, GUARD_B_OLD = 0x20D7E2, bytes.fromhex('2d41ffe46f0002a6')
SAMPLE_BASE_GLOBAL = 0x29F38E       # read by the guards
# Both sit in DSP2 bus area 2 (below 0x1C0000, about 1 wait state). Area 3
# (0x1C0000 up) costs about 5 per access and per code-fetch miss on hardware.
DELAY_POOL = (0x190000, 0x20000)    # optional per-track delay rings (16 x 0x2000 words)
CODE_REGION = 0x1B0000              # reserved for custom DSP banks (16 x 0x1000 words)

# ---- The E12 layout (the examples) ---------------------------------------------------
# E12's sample data sits below all sample memory, in bus area 0 (about 1 wait state,
# as area 2). With the 16 E12 machines removed it holds a delay pool and 17 program
# banks, and sample memory, budgets and the RAM machines stay stock.
E12_DATA = (0x103DBA, 201804)       # 42 P sections: 21 samples, each with a 0x99-word tail
E12_SECTIONS = 42
E12_POOL = (0x104000, 0x20000)      # 16 x 0x2000-word per-track rings, 8K-aligned
E12_CODE_REGION = (0x124000, 0x11000)   # 17 banks of 0x1000; RAM staging starts at 0x135206


def sample_reservation(reserve_from):
    """New (ROM, total) budgets per branch so that sample memory ends at
    `reserve_from`. Raises if a branch cannot keep its stock RAM minimum."""
    plan = {}
    for branch, (_, form, rom, _, total, base) in BUDGET_SITES.items():
        new_total = 2 * (reserve_from - base)
        if not 0 < new_total <= total:
            raise ImageError(f'{branch}: 0x{reserve_from:x} is outside its sample memory')
        new_rom = rom - (total - new_total)            # keep total - ROM (the RAM minimum)
        if form == 'moveq-swap':                        # moveq #n / swap: a multiple of 0x10000
            new_rom &= ~0xFFFF
        if new_rom <= 0:
            raise ImageError(f'{branch}: reservation leaves no ROM budget')
        plan[branch] = {'rom': new_rom, 'total': new_total, 'base': base,
                        'ram_minimum_words': (new_total - new_rom) // 2}
    return plan


def reserve_sample_memory(mainos, reserve_from, guard_a_cpu, guard_b_cpu):
    """Make DSP2 memory from `reserve_from` up unavailable to samples on both model
    branches: lower the budgets and install the loader guards. The guard code
    (see tools/examples/common/guards.s) must use the same ceiling. RAM machines
    keep working; their recording capacity shrinks by the reserved amount."""
    plan = sample_reservation(reserve_from)
    for branch, (rom_site, form, rom, total_site, total, _) in BUDGET_SITES.items():
        new = plan[branch]
        if form == 'moveq-swap':
            mainos_replace(mainos, rom_site, bytes([0x70, rom >> 16]), bytes([0x70, new['rom'] >> 16]))
        else:
            mainos_word(mainos, rom_site, rom, new['rom'])
        mainos_word(mainos, total_site, total, new['total'])
    mainos_replace(mainos, GUARD_A_SITE, GUARD_A_OLD, b'\x4e\xb9' + u32(guard_a_cpu))
    mainos_replace(mainos, GUARD_B_SITE, GUARD_B_OLD, b'\x4e\xb9' + u32(guard_b_cpu) + b'\x4e\x71')
    return plan


def remove_ram_machines(mainos, dsp):
    """Drop RAM-R1..R4 and RAM-P1..P4 (IDs 160-163, 165-168): ID table to the
    empty descriptor, DSP dispatch to the fallback. Only for builds that trade
    RAM recording for delay-line memory (see docs/12-packing-firmware.md);
    ordinary custom machines leave the RAM machines alone."""
    for ident in RAM_IDS:
        unregister_id(mainos, ident)
        clear_dispatch(dsp, ident + 1)


def e12_sections(dsp):
    """E12's stock sample sections: P sections wholly inside E12_DATA."""
    start, count = E12_DATA
    hits = [s for s in dsp.sections if s.space == 'P' and start <= s.address
            and s.address + s.count <= start + count]
    if len(hits) != E12_SECTIONS or sum(s.count for s in hits) != count:
        raise ImageError('E12 sample sections not found: already removed, or custom sections inside?')
    return [(s.space, s.address, s.count) for s in hits]


def remove_e12_machines(mainos, dsp):
    """Free E12's sample data for custom code and delay pools (docs/12-packing-
    firmware.md): IDs 48-63 to the empty descriptor, DSP types 49-64 to the
    fallback, the 42 sample sections out of the upload, and the E12 record out of
    the family table in place (later families move up one; the OS identifies
    families by ID, and rebuilds its inverse map from the lists at boot). Call it
    before adding programs to the region. Returns the number of words removed."""
    families = read_families(mainos)
    if families[E12_FAMILY_INDEX][1] != 'E12':
        raise ImageError('family 3 is not E12')
    for ident in E12_IDS:
        unregister_id(mainos, ident)
        clear_dispatch(dsp, ident + 1)
    rest = b''.join(name.encode().ljust(4, b'\0') + u32(p)
                    for i, name, p in families if i != E12_FAMILY_INDEX)
    old = mainos_read(mainos, FAMILY_TABLE, 8 * len(families) + 8)
    mainos_replace(mainos, FAMILY_TABLE, old, rest + bytes(16))
    return dsp.remove_sections(e12_sections(dsp))


LABEL_RE = re.compile('[A-Z0-9 -]{0,4}')
NAME_RE = re.compile('[A-Z0-9-]')


def u32(value):
    return value.to_bytes(4, 'big')


def descriptor(mainos, handler, ident, family, suffix, labels, defaults,
               format_bytes=b'\x11\x11\x11\x11'):
    """86-byte descriptor built on the stock GND-SIN record."""
    if len(family) != 3 or len(suffix) != 2 or not all(NAME_RE.fullmatch(c) for c in family + suffix):
        raise ImageError('family is 3 and suffix 2 uppercase characters')
    if len(labels) != 8 or len(defaults) != 8:
        raise ImageError('eight labels and eight defaults')
    d = bytearray(mainos_read(mainos, GND_SIN_DESCRIPTOR, DESCRIPTOR_SIZE))
    if d[4] != 1 or d[5:10] != b'GNDSN':
        raise ImageError('GND-SIN template not found: wrong MainOS?')
    d[0:4] = u32(handler)
    d[4] = ident
    d[5:8] = family.encode()
    d[8:10] = suffix.encode()
    for i, label in enumerate(labels):
        if not LABEL_RE.fullmatch(label):
            raise ImageError(f'label {label!r}: up to 4 of A-Z 0-9 space -')
        d[10 + 4 * i:14 + 4 * i] = label.encode().ljust(4, b'\0')
    if any(not 0 <= v <= 127 for v in defaults):
        raise ImageError('defaults are 0..127')
    d[42:50] = bytes(defaults)
    d[50:54] = bytes(format_bytes)
    return bytes(d)


def menu_list(pointers):
    """Zero-terminated list of descriptor pointers."""
    return b''.join(u32(p) for p in pointers) + bytes(4)


def read_list(mainos, cpu_address, limit=256):
    out = []
    for i in range(limit):
        v = int.from_bytes(mainos_read(mainos, cpu_address + 4 * i), 'big')
        if v == 0:
            return out
        out.append(v)
    raise ImageError('unterminated list')


def read_families(mainos):
    """[(index, name, list_pointer)] from the stock family table."""
    out = []
    for i in range(64):
        rec = mainos_read(mainos, FAMILY_TABLE + 8 * i, 8)
        if rec == bytes(8):
            return out
        out.append((i, rec[:4].rstrip(b'\0').decode('ascii'), int.from_bytes(rec[4:], 'big')))
    raise ImageError('unterminated family table')


def register_id(mainos, ident, descriptor_cpu, dsp=None):
    """ID table entry: empty descriptor -> new descriptor. With `dsp`, also check
    that DSP type ident+1 is unused (all three cells on the fallback entries)."""
    if ident not in FREE_IDS:
        raise ImageError(f'ID {ident} is not a verified free ID: 4-15, 30-31, 40-47, 73-79, 86-94')
    if dsp is not None and not dsp_type_free(dsp, ident + 1):
        raise ImageError(f'DSP type {ident + 1} of ID {ident} is already used')
    mainos_word(mainos, ID_TABLE + 4 * ident, EMPTY_DESCRIPTOR, descriptor_cpu)


def unregister_id(mainos, ident):
    """Point an ID back at the empty descriptor (for example the RAM machines)."""
    addr = ID_TABLE + 4 * ident
    old = int.from_bytes(mainos_read(mainos, addr), 'big')
    mainos_word(mainos, addr, old, EMPTY_DESCRIPTOR)
    return old


def rename_family(mainos, index, old, new):
    mainos_replace(mainos, FAMILY_TABLE + 8 * index, old.encode().ljust(4, b'\0'),
                   new.encode().ljust(4, b'\0'))


def set_family_list(mainos, index, old_pointer, new_pointer):
    mainos_word(mainos, FAMILY_TABLE + 8 * index + 4, old_pointer, new_pointer)


def relocate_family_table(mainos, new_table_cpu, extra):
    """Copy the family table plus `extra` [(name, list_pointer)] and a zero
    record; repoint the eight references. Returns the new table's bytes."""
    count = len(read_families(mainos))          # STOCK_FAMILIES, or one less without E12
    table = bytearray(mainos_read(mainos, FAMILY_TABLE, 8 * count))
    for name, pointer in extra:
        table += name.encode().ljust(4, b'\0') + u32(pointer)
    table += bytes(8)
    for ref in FAMILY_TABLE_REFS:
        mainos_word(mainos, ref, FAMILY_TABLE, new_table_cpu)
    for ref in FAMILY_LIST_REFS:
        mainos_word(mainos, ref, FAMILY_TABLE + 4, new_table_cpu + 4)
    return bytes(table)


# ---- DSP2 edits --------------------------------------------------------------------

def dsp_type_free(dsp, dsp_type):
    """True when all three dispatch cells of `dsp_type` hold their table's fallback."""
    return all(dsp.read(t + dsp_type) == dsp.read(t) for t in DISPATCH_TABLES)


def set_dispatch(dsp, dsp_type, entries):
    """Point one DSP type's init/update/render cells at `entries`. Each cell must
    still hold its table's fallback (entry 0)."""
    for table, entry in zip(DISPATCH_TABLES, entries):
        dsp.write(table + dsp_type, entry, expected=dsp.read(table))


def clear_dispatch(dsp, dsp_type):
    """Point a type's cells back at the fallback entries."""
    for table in DISPATCH_TABLES:
        dsp.write(table + dsp_type, dsp.read(table))


def add_program(dsp, bank, capacity, code, reserved=()):
    """Upload `code` at P:bank; refuse overlaps with stock sections or `reserved`
    [(start, count)] regions."""
    if len(code) > capacity:
        raise ImageError(f'program ({len(code)} words) exceeds its bank ({capacity})')
    if dsp.overlaps(bank, capacity):
        raise ImageError(f'bank 0x{bank:06x} overlaps an uploaded section')
    for start, count in reserved:
        if start < bank + capacity and bank < start + count:
            raise ImageError(f'bank 0x{bank:06x} overlaps a reserved region')
    dsp.append_section('P', bank, code)


# ---- assembler output ----------------------------------------------------------------

def parse_lod(text):
    """a56-style LOD from dsp56300-asm: `P addr word` records and `I addr name`
    symbols. Returns ({(space, addr): word}, {name: addr})."""
    memory, symbols = {}, {}
    for line in text.splitlines():
        f = line.split()
        if not f:
            continue
        if len(f) != 3:
            raise ImageError(f'malformed LOD line {line!r}')
        space, address, value = f
        if space == 'I':
            if value in symbols:
                raise ImageError(f'duplicate symbol {value}')
            symbols[value] = int(address, 16)
        elif space in 'PXYL':
            key = (space, int(address, 16))
            if key in memory:
                raise ImageError('overlapping assembler segments')
            memory[key] = int(value, 16) & 0xFFFFFF
        else:
            raise ImageError(f'unknown space {space}')
    return memory, symbols


def program_from_lod(text, bank, capacity):
    """Validate one contiguous P segment at `bank` and return (words, entries, symbols)."""
    memory, symbols = parse_lod(text)
    if any(space != 'P' for space, _ in memory):
        raise ImageError('only P records are supported')
    addresses = sorted(a for _, a in memory)
    if addresses != list(range(bank, bank + len(addresses))):
        raise ImageError('program is not one contiguous segment at the bank')
    if len(addresses) > capacity:
        raise ImageError('program exceeds its bank')
    words = [memory['P', a] for a in addresses]
    try:
        entries = [symbols[s] for s in ENTRY_SYMBOLS]
    except KeyError as e:
        raise ImageError(f'missing entry symbol {e}') from None
    if any(not bank <= e < bank + len(words) for e in entries):
        raise ImageError('entry outside the program')
    return words, entries, symbols
