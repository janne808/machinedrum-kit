"""OS 1.63 image layout: blob chain, DSP upload streams, packing and checks.

See docs/03-firmware-image.md and docs/12-packing-firmware.md.
"""
import hashlib
import struct

from .codec import CodecError, build_blob, compress, decompress, read_blob

STOCK_SHA256 = '68542e30917b9918ccaee2b2237df62c8a00479938680b85aca93ce4fbca44c8'
IMAGE_SIZE = 0x800000
CHAIN_START = 0x4000
OS_FLASH_END = 0x100000             # the updater's normal range ends here
MAIN_BASE = 0x200000                # MainOS CPU address
FLASH_CPU_BASE = 0x10000000         # CS0 alias used by the running OS
ORDER = ('MainOS', 'DSP2', 'DSP1', 'OS_A', 'OS_B')
UPDATER_MIN_VERSION = 0x3134301F    # incoming version must exceed "140\x1F"
VERSION_CHARS = frozenset('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ ')


class ImageError(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def flash_cpu(offset):
    """CPU address at which the running OS reads flash file offset `offset`."""
    if not 0 <= offset < IMAGE_SIZE:
        raise ImageError('flash offset outside the 8 MiB device')
    return FLASH_CPU_BASE + offset


# ---- blob chain ---------------------------------------------------------------

def discover_blobs(image):
    """Walk the chain from 0x4000. Returns ({name: record}, version_string)."""
    def header(name, offset):
        if offset + 8 > len(image):
            raise ImageError(f'{name}: header outside image')
        size, checksum = struct.unpack_from('>II', image, offset)
        if offset + 8 + size > len(image):
            raise ImageError(f'{name}: data outside image')
        return {'name': name, 'offset': offset, 'size': size, 'checksum': checksum,
                'end': offset + 8 + size}
    blobs = {}
    p = CHAIN_START
    for name in ('MainOS', 'DSP2', 'DSP1'):
        blobs[name] = header(name, p)
        p = blobs[name]['end']
    version = bytes(image[p:p + 4]).decode('ascii', errors='replace')
    blobs['version'] = {'name': 'version', 'offset': p, 'size': 4, 'end': p + 4}
    p += 4
    for name in ('OS_A', 'OS_B'):
        blobs[name] = header(name, p)
        p = blobs[name]['end']
    return blobs, version


def extract(image):
    """Verify every checksum and return {name: decompressed payload} plus a report."""
    if len(image) != IMAGE_SIZE:
        raise ImageError('expected an 8 MiB image')
    blobs, version = discover_blobs(image)
    payloads, report = {}, {'sha256': sha256(image), 'stock': sha256(image) == STOCK_SHA256,
                            'version': version, 'blobs': []}
    for name in ORDER:
        b = blobs[name]
        data, checksum = read_blob(image, b['offset'])
        if sum(data) & 0xFFFFFFFF != checksum:
            raise ImageError(f'{name}: compressed-byte checksum mismatch')
        payload = decompress(data, max_output=4 * 1024 * 1024)
        payloads[name] = payload
        entry = dict(b, decompressed=len(payload), sha256=sha256(payload))
        if name.startswith('DSP'):
            s = DspStream(payload)
            entry['dsp'] = {'entry': s.entry, 'config': s.config,
                            'sections': [{'space': x.space, 'address': x.address, 'words': x.count}
                                         for x in s.sections]}
        report['blobs'].append(entry)
    return payloads, report


# ---- DSP upload stream ---------------------------------------------------------

class Section:
    __slots__ = ('space', 'address', 'count', 'byte_offset')

    def __init__(self, space, address, count, byte_offset):
        self.space, self.address, self.count, self.byte_offset = space, address, count, byte_offset

    def covers(self, address):
        return self.address <= address < self.address + self.count


def words_le(words):
    return b''.join((w & 0xFFFFFF).to_bytes(3, 'little') for w in words)


class DspStream:
    """A decompressed DSP blob: preamble 3,entry,4,config; sections; terminator 3,entry."""

    def __init__(self, data):
        data = bytes(data)
        if len(data) % 3:
            raise ImageError('DSP stream is not whole 24-bit words')
        self.data = bytearray(data)
        self._parse()

    def _word(self, i):
        return int.from_bytes(self.data[3 * i:3 * i + 3], 'little')

    def _parse(self):
        n = len(self.data) // 3
        if n < 6 or self._word(0) != 3 or self._word(2) != 4:
            raise ImageError('missing entry/config preamble')
        self.entry, self.config = self._word(1), self._word(3)
        self.sections, pos = [], 4
        while pos < n:
            tag = self._word(pos); pos += 1
            if tag == 3:
                if pos + 1 != n or self._word(pos) != self.entry:
                    raise ImageError('bad terminator or entry mismatch')
                return
            if tag not in (0, 1, 2) or pos + 2 > n:
                raise ImageError('bad section header')
            address, count = self._word(pos), self._word(pos + 1); pos += 2
            if pos + count > n or address + count > 0x1000000:
                raise ImageError('section outside stream or address space')
            self.sections.append(Section('PXY'[tag], address, count, 3 * pos))
            pos += count
        raise ImageError('missing terminator')

    def locate(self, address, space=None, alias_base=0x20000):
        """Byte offset of the single uploaded word at `address`.

        Below alias_base only sections of `space` count; above it P/X/Y alias,
        so every section counts."""
        hits = [s for s in self.sections if s.covers(address)
                and (address >= alias_base or space is None or s.space == space)]
        if len(hits) != 1:
            raise ImageError(f'{space or "?"}:0x{address:06x} is uploaded {len(hits)} times')
        s = hits[0]
        return s.byte_offset + 3 * (address - s.address)

    def read(self, address, space=None):
        off = self.locate(address, space)
        return int.from_bytes(self.data[off:off + 3], 'little')

    def write(self, address, value, expected=None, space=None):
        """Overwrite one uploaded word, optionally compare-before-write."""
        off = self.locate(address, space)
        old = int.from_bytes(self.data[off:off + 3], 'little')
        if expected is not None and old != expected:
            raise ImageError(f'0x{address:06x}: expected 0x{expected:06x}, found 0x{old:06x}')
        self.data[off:off + 3] = (value & 0xFFFFFF).to_bytes(3, 'little')
        return old

    def overlaps(self, start, count, alias_base=0x20000):
        """Uploaded sections that touch [start, start+count) in the aliased region
        (or in P space below the alias base)."""
        return [s for s in self.sections
                if (s.space == 'P' or s.address >= alias_base)
                and s.address < start + count and start < s.address + s.count]

    def append_section(self, space, address, words):
        """Insert a section immediately before the terminator."""
        tag = 'PXY'.index(space)
        term = bytes(self.data[-6:])
        self.data[-6:] = words_le([tag, address, len(words)] + list(words)) + term
        entry, config = self.entry, self.config
        self._parse()
        if (self.entry, self.config) != (entry, config):
            raise ImageError('entry/config changed')

    def bytes(self):
        return bytes(self.data)


# ---- ColdFire payload edits ------------------------------------------------------

def guard_replace(buf, offset, expected, replacement):
    """Compare-before-write, same length."""
    expected, replacement = bytes(expected), bytes(replacement)
    if len(expected) != len(replacement):
        raise ImageError('replacement must keep the length')
    if bytes(buf[offset:offset + len(expected)]) != expected:
        raise ImageError(f'precondition mismatch at file offset 0x{offset:x}')
    buf[offset:offset + len(expected)] = replacement


def mainos_replace(mainos, cpu_address, expected, replacement):
    """guard_replace at a MainOS CPU address (file offset = address - 0x200000)."""
    guard_replace(mainos, cpu_address - MAIN_BASE, expected, replacement)


def mainos_word(mainos, cpu_address, expected, value):
    mainos_replace(mainos, cpu_address, expected.to_bytes(4, 'big'), value.to_bytes(4, 'big'))


def mainos_read(mainos, cpu_address, n=4):
    off = cpu_address - MAIN_BASE
    return bytes(mainos[off:off + n])


# ---- version ---------------------------------------------------------------------

def check_version(version):
    if not isinstance(version, str) or len(version) != 4:
        raise ImageError('version must be exactly 4 characters, e.g. "163N"')
    if not set(version) <= VERSION_CHARS or version[0] == ' ':
        raise ImageError('version: digits, uppercase letters and spaces; no leading space')
    if int.from_bytes(version.encode('ascii'), 'big') <= UPDATER_MIN_VERSION:
        raise ImageError('version must sort above "140" or the updater rejects the file')
    return version


# ---- packing -----------------------------------------------------------------------

def pack_image(original, replacements, banks, version=None, margin=0x100):
    """Rebuild the chain with recompressed `replacements` ({name: payload}), write
    `banks` ({flash_offset: bytes}) into the erased tail, keep everything else.

    Returns (image, records)."""
    if len(original) != IMAGE_SIZE:
        raise ImageError('expected an 8 MiB source image')
    unknown = set(replacements) - set(ORDER)
    if unknown:
        raise ImageError(f'unknown blob names {sorted(unknown)}')
    blobs, stock_version = discover_blobs(original)
    version = stock_version if version is None else check_version(version)
    stock_end = blobs['OS_B']['end']
    lowest_bank = min(list(banks) + [OS_FLASH_END])
    if original[stock_end:OS_FLASH_END] != b'\xff' * (OS_FLASH_END - stock_end):
        raise ImageError('source flash tail is not erased')
    spans = sorted((o, o + len(d)) for o, d in banks.items())
    for (a0, a1), (b0, _) in zip(spans, spans[1:]):
        if a1 > b0:
            raise ImageError(f'flash banks overlap at 0x{b0:x}')
    if spans and spans[-1][1] > OS_FLASH_END:
        raise ImageError('flash bank beyond 0x100000')
    chain, records = bytearray(), []
    for name in ORDER:
        if name == 'OS_A':
            chain += version.encode('ascii')
        b = blobs[name]
        if name in replacements:
            payload = bytes(replacements[name])
            stream = compress(payload)
            if decompress(stream, max_output=len(payload) + 1) != payload:
                raise ImageError(f'{name}: compressor round trip failed')
            record = build_blob(stream)
        else:
            record = bytes(original[b['offset']:b['end']])
        records.append({'name': name, 'offset': CHAIN_START + len(chain), 'size': len(record),
                        'sha256': sha256(record)})
        chain += record
    if CHAIN_START + len(chain) + margin > lowest_bank:
        raise ImageError(f'blob chain (ends 0x{CHAIN_START + len(chain):x}) runs into the '
                         f'flash banks at 0x{lowest_bank:x}')
    image = bytearray(original)
    image[CHAIN_START:OS_FLASH_END] = b'\xff' * (OS_FLASH_END - CHAIN_START)
    image[CHAIN_START:CHAIN_START + len(chain)] = chain
    for offset, data in banks.items():
        image[offset:offset + len(data)] = data
    image = bytes(image)
    if image[:CHAIN_START] != original[:CHAIN_START] or image[OS_FLASH_END:] != original[OS_FLASH_END:]:
        raise ImageError('boot sector or upper flash changed')
    if discover_blobs(image)[1] != version:
        raise ImageError('version record did not read back')
    return image, records


# ---- checks ------------------------------------------------------------------------

def low_window_pointers(image, banks, mainos_edits=()):
    """Words in the flash banks (and MainOS edit values) that point into the low
    flash window between the lowest bank and 0x100000. On hardware nothing
    decodes that window after the CS0 remap; such pointers hang the OS."""
    if not banks:
        return []
    low = min(banks)
    hits = []
    for offset, data in banks.items():
        for i in range(0, len(data) - 3, 2):
            v = int.from_bytes(data[i:i + 4], 'big')
            if low <= v < OS_FLASH_END:
                hits.append(('bank', offset + i, v))
    for address, value in mainos_edits:
        if low <= value < OS_FLASH_END:
            hits.append(('mainos', address, value))
    return hits


def verify_image(image, original, expected_payloads=None):
    """Re-extract `image` and compare: checksums, payloads, untouched regions."""
    payloads, report = extract(image)
    problems = []
    if image[:CHAIN_START] != original[:CHAIN_START]:
        problems.append('boot sector differs from the source')
    if image[OS_FLASH_END:] != original[OS_FLASH_END:]:
        problems.append('flash above 0x100000 differs from the source')
    for name, payload in (expected_payloads or {}).items():
        if payloads[name] != bytes(payload):
            problems.append(f'{name} does not decompress to the intended payload')
    return payloads, report, problems
