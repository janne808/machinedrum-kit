"""Machinedrum OS 1.63 blob codec (LZSS family) and blob records.

decompress() mirrors the boot-sector routine at flash 0x1C4. compress() is a
greedy hash-chain encoder with lazy matching whose output decompress() accepts;
it is not bit-identical to Elektron's encoder, and does not need to be.
See docs/04-compression.md for the format.
"""
import struct
from collections import deque

EOS_CODE = 0x1000002    # distance code whose raw value is 0xFFFFFFFF: end of stream
MAX_MATCH = 8192        # practical upper bound on one copy
CHAIN2_CAP = 64         # 2-byte hash chain depth (short distances only)
CHAIN3_CAP = 1024       # 3-byte hash chain depth


class CodecError(ValueError):
    pass


# ---- decoder -----------------------------------------------------------------

def decompress(stream, max_output=16 * 1024 * 1024):
    """Decompress a raw compressed stream (no 8-byte header). Returns bytes."""
    data = bytes(stream)
    pos = 0
    buf = 0
    nbits = 0
    out = bytearray()

    def read_bit():
        nonlocal pos, buf, nbits
        if nbits == 0:
            if pos >= len(data):
                raise CodecError(f'compressed stream overrun at 0x{pos:x}')
            buf = data[pos]; pos += 1; nbits = 8
        bit = (buf >> 7) & 1
        buf = (buf << 1) & 0xFF
        nbits -= 1
        return bit

    def vlq():
        acc = 1
        while True:
            acc = acc * 2 + read_bit()
            if acc > 0xFFFFFFFF:
                raise CodecError('VLQ exceeds the 32-bit accumulator')
            if read_bit():
                return acc

    prev_dist = 1
    while True:
        if read_bit():                                  # literal
            if pos >= len(data):
                raise CodecError('literal past end of stream')
            if len(out) >= max_output:
                raise CodecError('output limit exceeded')
            out.append(data[pos]); pos += 1
            continue
        code = vlq()                                    # match
        if code == 2:
            dist = prev_dist
        else:
            if pos >= len(data):
                raise CodecError('extension byte past end of stream')
            raw = (code * 256 + data[pos] - 0x300) & 0xFFFFFFFF
            if raw == 0xFFFFFFFF:                       # end marker (byte peeked only)
                return bytes(out)
            pos += 1
            dist = raw + 1
            prev_dist = dist
        lc = read_bit() * 2 + read_bit()
        if lc == 0:
            lc = vlq() + 2
        if dist > 0xD00:
            lc += 1
        n = lc + 1
        if n > max_output - len(out):
            raise CodecError('output limit exceeded')
        src = len(out) - dist
        if src < 0:
            raise CodecError(f'back-reference before start (dist {dist} at {len(out)})')
        for _ in range(n):
            out.append(out[src]); src += 1


# ---- encoder -----------------------------------------------------------------

class BitWriter:
    """
    MSB-first bit writer with interleaved raw bytes.

    A single reserved slot in the output array holds the current control
    byte.  raw() appends bytes directly WITHOUT reserving a new slot —
    the caller is responsible for ensuring a preceding bit() already owns
    the current slot.
    """
    __slots__ = ('_out', '_buf', '_n', '_slot')

    def __init__(self):
        self._out  = bytearray()
        self._buf  = 0      # bits accumulated MSB-first
        self._n    = 0      # bits accumulated (0..7)
        self._slot = None   # index of current reserved control byte

    def _ensure_slot(self):
        if self._slot is None:
            self._slot = len(self._out)
            self._out.append(0)

    def bit(self, b: int):
        """Write one control bit."""
        self._ensure_slot()
        self._buf = (self._buf << 1) | (int(b) & 1)
        self._n  += 1
        if self._n == 8:
            self._out[self._slot] = self._buf
            self._buf  = 0
            self._n    = 0
            self._slot = None

    def raw(self, b: int):
        """Append a raw byte (literal or ext_byte) to the stream.
        Must be called after at least one bit() that established the slot."""
        self._out.append(b & 0xFF)

    def flush_and_eos(self):
        """Emit EOS token (flag=0, VLQ(0x1000002)), flush partial byte, append 0xFF."""
        self.bit(0)                       # flag=0 (match, not literal)
        _vlq_write(self, EOS_CODE)        # VLQ code that yields dist_raw=0xFFFFFFFF
        if self._n > 0:
            self._out[self._slot] = self._buf << (8 - self._n)
            self._buf  = 0
            self._n    = 0
            self._slot = None
        self._out.append(0xFF)            # peeked as ext_byte; never consumed

    def data(self) -> bytes:
        return bytes(self._out)



def _vlq_write(bw: BitWriter, code: int):
    """Emit VLQ-encoded integer.  code ≥ 2: code = 2^n + k → n pairs (data,cont)."""
    n = code.bit_length() - 1
    k = code ^ (1 << n)
    for i in range(n - 1, -1, -1):
        bw.bit((k >> i) & 1)
        bw.bit(0 if i > 0 else 1)


# ─── Token emitters ───────────────────────────────────────────────────────────

def _emit_literal(bw: BitWriter, byte: int):
    bw.bit(1)
    bw.raw(byte)


def _emit_match(bw: BitWriter, dist: int, n: int, prev_dist: int) -> int:
    """Emit match token; returns new prev_dist."""
    bw.bit(0)

    if dist == prev_dist:
        _vlq_write(bw, 2)                      # REUSE — no ext_byte
    else:
        val = dist - 1 + 0x300
        _vlq_write(bw, val >> 8)
        bw.raw(val & 0xFF)
        prev_dist = dist

    # Length: adjust for the decompressor's long-distance +1
    lc_raw = n - 1 - (1 if dist > 0xD00 else 0)
    assert lc_raw >= 1, f"n={n} dist={dist} → lc_raw={lc_raw} < 1"

    if lc_raw <= 3:
        bw.bit((lc_raw >> 1) & 1)
        bw.bit( lc_raw       & 1)
    else:
        bw.bit(0); bw.bit(0)
        _vlq_write(bw, lc_raw - 2)

    return prev_dist


# ─── Match length helper ──────────────────────────────────────────────────────

def _mlen(data: bytes, src: int, pos: int, limit: int) -> int:
    """Count matching bytes data[src..] vs data[pos..], up to limit."""
    end = min(limit, len(data) - pos)
    l = 0
    while l < end and data[src + l] == data[pos + l]:
        l += 1
    return l


# ─── Compressor ───────────────────────────────────────────────────────────────

def compress(data: bytes) -> bytes:
    """
    Compress *data* using the Machinedrum LZSS algorithm.

    Returns the raw compressed byte stream (no header).
    The caller must prepend the 8-byte blob header if needed.
    """
    total     = len(data)
    bw        = BitWriter()
    prev_dist = 1

    # Hash chains: int key → deque of positions, oldest-first.
    # reversed(chain) iterates newest-first (smallest distance first).
    chains2: dict = {}   # 16-bit key (2-byte prefix) → deque
    chains3: dict = {}   # 24-bit key (3-byte prefix) → deque

    def _add(p: int):
        """Add position p to the appropriate hash chains."""
        if p + 1 < total:
            k2 = data[p] << 8 | data[p + 1]
            ch = chains2.get(k2)
            if ch is None:
                chains2[k2] = deque([p], maxlen=CHAIN2_CAP)
            else:
                ch.append(p)
        if p + 2 < total:
            k3 = data[p] << 16 | data[p + 1] << 8 | data[p + 2]
            ch = chains3.get(k3)
            if ch is None:
                chains3[k3] = deque([p], maxlen=CHAIN3_CAP)
            else:
                ch.append(p)

    def _find(p: int):
        """Return (reuse_len, best_len, best_dist) for position p."""
        rem = total - p

        reuse_len = 0
        if prev_dist <= p:
            l = _mlen(data, p - prev_dist, p, MAX_MATCH)
            min_l = 3 if prev_dist > 0xD00 else 2
            if l >= min_l:
                reuse_len = l

        best_len  = 0
        best_dist = 0

        if rem >= 3:
            k3 = data[p] << 16 | data[p + 1] << 8 | data[p + 2]
            ch = chains3.get(k3)
            if ch:
                for cand in reversed(ch):
                    d = p - cand
                    if d <= 0 or d == prev_dist:
                        continue
                    min_l = 3 if d > 0xD00 else 2
                    l = _mlen(data, cand, p, MAX_MATCH)
                    if l >= min_l and (l > best_len or
                                       (l == best_len and d < best_dist)):
                        best_len  = l
                        best_dist = d

        if rem >= 2:
            k2 = data[p] << 8 | data[p + 1]
            ch = chains2.get(k2)
            if ch:
                for cand in reversed(ch):
                    d = p - cand
                    if d <= 0 or d == prev_dist or d > 0xD00:
                        continue
                    l = _mlen(data, cand, p, MAX_MATCH)
                    if l >= 2 and (l > best_len or
                                   (l == best_len and d < best_dist)):
                        best_len  = l
                        best_dist = d

        return reuse_len, best_len, best_dist

    pos = 0
    while pos < total:
        _add(pos)   # pre-insert before searching (affects chain state for look-ahead)

        rl, bl, bd = _find(pos)
        use_reuse = (rl > 0 and
                     (rl > bl or (rl == bl and prev_dist < bd)))
        cur_len   = rl if use_reuse else (bl if bl > 0 else 0)

        # ─── Lazy evaluation: only for non-REUSE matches ────────────────────
        if not use_reuse and cur_len >= 2 and pos + 1 < total:
            rl1, bl1, bd1 = _find(pos + 1)
            use_r1     = (rl1 > 0 and
                          (rl1 > bl1 or (rl1 == bl1 and prev_dist < bd1)))
            next_len1  = rl1 if use_r1 else (bl1 if bl1 > 0 else 0)
            next_dist1 = prev_dist if use_r1 else bd1

            # VLQ cost group for a distance: bit_length of its distance code.
            # dist 1-256 → code=3 → group 2; dist 257-1280 → code 4-7 → group 3.
            # A lower group means fewer VLQ bits to encode the distance.
            _vlqg = lambda d: ((d - 1 + 0x300) >> 8).bit_length()

            # 1-step lazy: compare greedy best (bd/cur_len) vs look-ahead
            # best (next_dist1/next_len1).  Structured by group jump:
            #
            #  group_jump < 0  (cheaper): gain=1 always worth it.
            #  group_jump = 0  (same):    gain≥2 always fires; gain=1 fires
            #                             only for cur_group ≤ 3 AND
            #                             (look-ahead is closer OR cur_len≤3).
            #  group_jump = +1 (1 dearer): gain≥2 fires; gain=1 fires when
            #                             cur_len ≤ 3 (short match costs less).
            #  group_jump ≥ 2:            require large gain (≥ 6).
            #  Any case: gain ≥ 6 always fires.
            #
            #  equal-length: REUSE (saves dist ext byte), or cur_dist in the
            #  code-7 range [1025-1280] with next in group 2 (dist ≤ 256).
            _g  = _vlqg(bd)
            _ng = _vlqg(next_dist1) if next_dist1 > 0 else 0
            _d  = next_len1 - cur_len   # gain (> 0 for strictly longer)
            _gj = _ng - _g              # group jump (+ve = more expensive)

            # Bit-length helpers for the cost-based lazy check.
            def _lb(n: int, d: int) -> int:
                lc = n - 1 - (1 if d > 0xD00 else 0)
                return 2 if lc <= 3 else 2 + 2 * ((lc - 2).bit_length() - 1)

            # Code band: (d-1+0x300)>>8 gives the VLQ code value; same band = same code.
            _code_bd   = (bd          - 1 + 0x300) >> 8 if bd > 0 else 0
            _code_bd1  = (next_dist1  - 1 + 0x300) >> 8 if next_dist1 > 0 else 0

            fire = False
            if _d > 0:
                if _d >= 6:
                    fire = True
                elif _gj < 0:                          # cheaper group: gain=1 ok
                    fire = True
                elif _gj == 0:                         # same group
                    if _d >= 2 or use_r1:              # gain≥2 or REUSE (saves ext)
                        fire = True
                    elif _g <= 4 and (
                        # same/closer code band: always for g=3, short-match for g=2 or g=4
                        (_code_bd1 <= _code_bd and (_g == 3 or cur_len <= 3)) or
                        # code 5+: allow two-band upward jump for short matches
                        (cur_len <= 3 and _code_bd >= 5 and _code_bd1 <= _code_bd + 2)):
                        fire = True
                elif _gj == 1:                         # one group dearer
                    if _d >= 4:                            # large gain: always
                        fire = True
                    elif _d >= 2 and _g == 2 and (  # grp 2→3: moderate next dist
                            _code_bd1 <= 6 or (_d >= 3 and _code_bd1 <= 7)):
                        fire = True
                    elif _d == 1 and _g == 3 and cur_len == 3 and _code_bd >= 7:
                        fire = True                        # grp 3 top-band, gain=1
                    elif _d == 1 and _g == 4 and cur_len == 3 and _code_bd == 15:
                        fire = True                        # grp 4 top-band, gain=1
                elif _gj == 2:                         # two groups dearer
                    if _d >= 3:
                        fire = True
            elif _d == 0 and next_len1 > 0:            # equal length
                fire = (use_r1 or
                        (bd >= 1025 and next_dist1 <= 256))
            elif _d == -1:                             # next is one shorter
                if use_r1:                             # REUSE: check bit cost
                    _vlq_code_bits = 2 * (_code_bd.bit_length() - 1)
                    fire = (_vlq_code_bits - 3 + _lb(cur_len, bd) - _lb(next_len1, prev_dist)) > 0
                elif _gj <= -3 and _code_bd >= 18:     # very far dist, 3+ groups cheaper
                    fire = True

            # 2-step (pos+1 no-match): fire only when escaping a very far match
            # (group 5+) to reach a very close match (group 2) at pos+2.
            if (not fire and next_len1 == 0 and _vlqg(bd) >= 5 and
                    pos + 2 < total):
                rl2, bl2, bd2 = _find(pos + 2)
                use_r2     = (rl2 > 0 and
                              (rl2 > bl2 or (rl2 == bl2 and prev_dist < bd2)))
                next_len2  = rl2 if use_r2 else (bl2 if bl2 > 0 else 0)
                next_dist2 = prev_dist if use_r2 else bd2
                fire = (next_len2 >= cur_len and
                        (_vlqg(next_dist2) <= 2 or use_r2))

            # 2-step (pos+1 equal-length): fire when pos+2 offers a dramatically longer match.
            if not fire and next_len1 == cur_len and pos + 2 < total:
                rl2, bl2, bd2 = _find(pos + 2)
                use_r2    = (rl2 > 0 and (rl2 > bl2 or (rl2 == bl2 and prev_dist < bd2)))
                next_len2 = rl2 if use_r2 else (bl2 if bl2 > 0 else 0)
                if next_len2 >= cur_len + 10 and cur_len >= 3:
                    fire = True

            # 2-step (pos+1 shorter): fire when pos+2 offers a cheaper VLQ group,
            # is a REUSE, or gains ≥4 bytes.
            if not fire and next_len1 > 0 and next_len1 < cur_len and pos + 2 < total:
                _add(pos + 1)   # pre-insert so pos+2 search can see dist=1 runs
                rl2, bl2, bd2 = _find(pos + 2)
                use_r2     = (rl2 > 0 and
                              (rl2 > bl2 or (rl2 == bl2 and prev_dist < bd2)))
                next_len2  = rl2 if use_r2 else (bl2 if bl2 > 0 else 0)
                next_dist2 = prev_dist if use_r2 else bd2
                fire = (
                    (next_len2 > cur_len and
                     (_vlqg(next_dist2) < _vlqg(bd) or
                      use_r2 or
                      next_len2 >= cur_len + 3 or
                      (next_len2 >= cur_len + 2 and next_dist2 <= bd and cur_len <= 3))) or
                    (use_r2 and next_len2 >= cur_len and cur_len <= 3)  # REUSE at pos+2
                )

            if fire:
                _emit_literal(bw, data[pos])
                pos += 1
                continue   # pos already pre-inserted; next iter pre-inserts pos+1

        # ─── Emit token ──────────────────────────────────────────────────────
        if use_reuse:
            prev_dist = _emit_match(bw, prev_dist, rl, prev_dist)
            adv       = rl
        elif bl > 0:
            prev_dist = _emit_match(bw, bd, bl, prev_dist)
            adv       = bl
        else:
            _emit_literal(bw, data[pos])
            adv = 1

        # pos already pre-inserted; add pos+1 through pos+adv-1
        for i in range(1, adv):
            _add(pos + i)
        pos += adv

    bw.flush_and_eos()
    return bw.data()




# ---- blob records --------------------------------------------------------------

def build_blob(compressed):
    """8-byte header (size, sum of compressed bytes) + compressed data."""
    compressed = bytes(compressed)
    return struct.pack('>II', len(compressed), sum(compressed) & 0xFFFFFFFF) + compressed


def read_blob(image, offset):
    """Return (compressed_bytes, stored_checksum) of the record at offset."""
    if offset < 0 or offset + 8 > len(image):
        raise CodecError('blob header outside image')
    size, checksum = struct.unpack_from('>II', image, offset)
    if offset + 8 + size > len(image):
        raise CodecError('blob data outside image')
    return bytes(image[offset + 8:offset + 8 + size]), checksum


def decompress_blob(image, offset, max_output=4 * 1024 * 1024):
    """Verify the compressed-byte checksum and decompress one record."""
    data, checksum = read_blob(image, offset)
    if sum(data) & 0xFFFFFFFF != checksum:
        raise CodecError(f'checksum mismatch in blob at 0x{offset:x}')
    return decompress(data, max_output)


def compress_checked(data):
    """compress() plus a full decode comparison; returns a complete blob record."""
    stream = compress(data)
    if decompress(stream, max_output=len(data) + 1) != bytes(data):
        raise CodecError('compressor round trip failed')
    return build_blob(stream)
