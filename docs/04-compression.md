# Compression

All five blobs use one LZSS-family codec, decoded by the boot sector's
`decompress(src, dest)` (flash `0x1C4`). `src` points at the 8-byte blob header;
compressed data starts at `src + 8`.

## Stream structure

The stream interleaves **control bytes** (bit-packed token fields) with **raw
bytes** (literals and distance extension bytes).

- Bits are consumed **MSB first**.
- When the decoder needs a bit and its bit buffer is empty, it takes **the next
  byte of the stream** as a new control byte. Raw bytes are also taken from the
  next stream position, whenever a token needs one. The two kinds therefore
  interleave in exactly the order the decoder asks for them.

## Decoder

```python
def read_bit():            # MSB-first; refills from the next stream byte
    if nbits == 0:
        buf = stream[pos]; pos += 1; nbits = 8
    bit = (buf >> 7) & 1; buf = (buf << 1) & 0xFF; nbits -= 1
    return bit

def read_byte():           # raw byte: literal or extension byte
    b = stream[pos]; pos += 1; return b

def vlq():                 # variable-length integer, result >= 2
    acc = 1
    while True:
        acc = acc * 2 + read_bit()      # data bit
        if read_bit() == 1:             # continuation bit: 1 = stop
            return acc

prev_dist = 1
while True:
    if read_bit() == 1:                 # literal
        out.append(read_byte())
        continue
    code = vlq()                        # match: distance code
    if code == 2:
        dist = prev_dist                # reuse previous distance
    else:
        ext = stream[pos]               # peek extension byte
        raw = (code * 256 + ext - 0x300) & 0xFFFFFFFF
        if raw == 0xFFFFFFFF:
            break                       # end of stream
        pos += 1                        # consume ext
        dist = raw + 1
        prev_dist = dist
    lc = read_bit() * 2 + read_bit()    # 2-bit length code, first bit is MSB
    if lc == 0:
        lc = vlq() + 2                  # extended length, >= 4
    if dist > 0xD00:
        lc += 1                         # long-distance bonus
    for _ in range(lc + 1):             # copy lc+1 bytes; overlap allowed
        out.append(out[-dist])
```

Notes:

- The end-of-stream marker is a match whose distance code gives
  `raw == 0xFFFFFFFF` (code `0x1000002` with extension `0xFF`). The extension byte
  is peeked, not consumed, when it signals the end.
- The minimum copy is 2 bytes (`lc = 1`), or 3 bytes at distances above `0xD00`.
- `decompress` returns 0 on success and 1 on failure; the bootloader reports a
  failure as `E: … CHECKSUM`. Which checks produce the failure (the header
  checksum, the end marker, or both) was not isolated. A correct image must
  satisfy both.

## Encoder

Any encoder that emits tokens the decoder above accepts will work. The boot
sector has no size limit on the compressed stream other than the checksum and
the flash layout. Rules for producing a valid stream:

1. Keep one **reserved control-byte slot** open. Write bits into it MSB-first.
   When 8 bits have been written, store the byte into its slot and reserve a new
   slot **at the current output position, but only when the next bit is
   written**.
2. Raw bytes (literals, extension bytes, the final `0xFF`) are appended at the
   current output position **without** reserving a slot. If the previous bit
   just filled a control byte, the raw byte goes straight after it. This matches
   the decoder, which only fetches a control byte when it needs a bit.
3. Tokens:

   | Token | Emitted as |
   | --- | --- |
   | Literal | bit `1`, raw byte |
   | Reuse previous distance | bit `0`, `vlq(2)`, length bits |
   | New distance | bit `0`, `vlq(code)`, raw `ext`, length bits |
   | End | bit `0`, `vlq(0x1000002)`, flush the partial control byte, raw `0xFF` |

   with `code = (dist − 1 + 0x300) >> 8` and `ext = (dist − 1 + 0x300) & 0xFF`
   for `dist ≥ 1`. That gives `code ≥ 3`, so a new distance never collides with
   the reuse code 2.
4. `vlq(n)` for `n ≥ 2`: write the bits of `n` below its leading 1, MSB first,
   each followed by a continuation bit that is `1` only after the last.
5. Length: for a copy of `n` bytes, `lc = n − 1 − (1 if dist > 0xD00 else 0)`.
   For `lc` 1..3 write two bits; for `lc ≥ 4` write `00` then `vlq(lc − 2)`.
   A copy that would need `lc < 1` is not encodable: emit literals instead.
6. After encoding, **decode the result and compare it byte for byte** with the
   input before using it. A mismatched round trip is the usual symptom of a
   slot-reservation bug in rule 1–2.

A greedy encoder with hash chains is adequate. It should check the
reuse-previous-distance token first (cheapest), then 3-byte matches, then
2-byte matches at short distances. Compression ratios close to the stock
blobs are reached this way. The exact stock encoder is unknown; bit-identical
recompression is not required.

## Building a record

```python
def build_blob(compressed):
    return (len(compressed).to_bytes(4, 'big')
            + (sum(compressed) & 0xFFFFFFFF).to_bytes(4, 'big')
            + compressed)
```
