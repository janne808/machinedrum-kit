"""mdkit command line: python3 -m mdkit <command> ...

  info    IMAGE                  blob chain, version, DSP sections
  extract IMAGE OUTDIR           verified payloads, DSP sections, manifest.json
  check   IMAGE [--stock STOCK]  checksums, CS0 low-window pointers, untouched regions, SysEx round trip
  roundtrip IMAGE                recompress every payload with mdkit and decode it again
  syx encode IMAGE OUT.syx       OS update SysEx from an 8 MiB image
  syx decode IN.syx OUT [--base STOCK]   raw payload, or a full image over a base
  syx inspect IN.syx             validate packets and show the payload extent
"""
import argparse
import json
import sys
from pathlib import Path

from . import codec, image as img, sysex


def write_new(path, data):
    with Path(path).open('xb') as f:
        f.write(data)


def cmd_info(a):
    data = a.image.read_bytes()
    blobs, version = img.discover_blobs(data)
    print(f'sha256   {img.sha256(data)}' + ('  (stock OS 1.63)' if img.sha256(data) == img.STOCK_SHA256 else ''))
    print(f'version  {version!r}')
    for name in ('MainOS', 'DSP2', 'DSP1', 'version', 'OS_A', 'OS_B'):
        b = blobs[name]
        print(f'{name:8} 0x{b["offset"]:06x}-0x{b["end"]:06x}  {b["end"] - b["offset"]:>8} bytes')
    tail = data[blobs['OS_B']['end']:img.OS_FLASH_END]
    used = [i for i in range(0, len(tail), 0x1000) if tail[i:i + 0x1000].strip(b'\xff')]
    if used:
        base = blobs['OS_B']['end']
        print('non-erased 4 KiB pages after the chain: ' +
              ', '.join(f'0x{(base + i) & ~0xFFF:05x}' for i in used))


def cmd_extract(a):
    data = a.image.read_bytes()
    payloads, report = img.extract(data)
    out = a.outdir
    out.mkdir(parents=True, exist_ok=False)
    for name, payload in payloads.items():
        (out / f'{name.lower()}.bin').write_bytes(payload)
        if name.startswith('DSP'):
            s = img.DspStream(payload)
            d = out / name.lower()
            d.mkdir()
            for i, sec in enumerate(s.sections):
                (d / f'{i:03d}-{sec.space}-{sec.address:06x}.bin').write_bytes(
                    payload[sec.byte_offset:sec.byte_offset + 3 * sec.count])
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{len(payloads)} payloads verified and written to {out}')


def cmd_check(a):
    data = a.image.read_bytes()
    stock = a.stock.read_bytes() if a.stock else None
    payloads, report = img.extract(data)
    problems = []
    blobs, _ = img.discover_blobs(data)
    chain_end = blobs['OS_B']['end']
    banks = {}
    tail = data[chain_end:img.OS_FLASH_END]
    i = 0
    while i < len(tail):                      # group non-erased runs into banks
        if tail[i] != 0xFF:
            j = i
            while j < len(tail) and tail[j:j + 64].strip(b'\xff'):
                j += 64
            banks[chain_end + i] = bytes(tail[i:j])
            i = j
        else:
            i += 1
    edits = []
    if stock is not None:
        stock_main = img.extract(stock)[0]['MainOS']
        main = payloads['MainOS']
        for off in range(0, len(main) - 3, 2):
            v = int.from_bytes(main[off:off + 4], 'big')
            if main[off:off + 4] != stock_main[off:off + 4]:
                edits.append((img.MAIN_BASE + off, v))
        if data[:img.CHAIN_START] != stock[:img.CHAIN_START]:
            problems.append('boot sector differs from the stock image')
        if data[img.OS_FLASH_END:] != stock[img.OS_FLASH_END:]:
            problems.append('flash above 0x100000 differs from the stock image')
    for kind, where, value in img.low_window_pointers(data, banks, edits):
        problems.append(f'{kind} 0x{where:x} holds low-window flash pointer 0x{value:x} '
                        f'(use 0x{img.flash_cpu(value):x})')
    payload = sysex.image_payload(data)
    decoded, _ = sysex.decode_sysex(sysex.encode_payload(payload))
    if decoded != payload:
        problems.append('SysEx round trip failed')
    print(f'checksums ok; version {report["version"]!r}; chain ends 0x{chain_end:x}; '
          f'{len(banks)} flash bank(s)' + ('' if stock else '; no --stock given, MainOS edits not scanned'))
    for p in problems:
        print('PROBLEM:', p)
    return 1 if problems else 0


def cmd_roundtrip(a):
    payloads, _ = img.extract(a.image.read_bytes())
    for name, payload in payloads.items():
        record = codec.compress_checked(payload)
        print(f'{name:7} {len(payload):>8} -> {len(record) - 8:>7} bytes  ok')


def cmd_syx(a):
    if a.action == 'encode':
        data = a.input.read_bytes()
        img.extract(data)
        payload = sysex.image_payload(data)
        encoded = sysex.encode_payload(payload)
        if sysex.decode_sysex(encoded)[0] != payload:
            raise SystemExit('transport round trip failed')
        write_new(a.output, encoded)
        print(f'{len(payload)} bytes (flash 0x4000-0x{0x4000 + len(payload):x}) -> {a.output}')
    elif a.action == 'decode':
        payload, report = sysex.decode_sysex(a.input.read_bytes())
        result = sysex.apply_payload(payload, a.base.read_bytes()) if a.base else payload
        write_new(a.output, result)
        print(json.dumps(report, indent=2))
    else:
        print(json.dumps(sysex.decode_sysex(a.input.read_bytes())[1], indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python3 -m mdkit', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('info'); p.add_argument('image', type=Path); p.set_defaults(fn=cmd_info)
    p = sub.add_parser('extract'); p.add_argument('image', type=Path); p.add_argument('outdir', type=Path)
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser('check'); p.add_argument('image', type=Path); p.add_argument('--stock', type=Path)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser('roundtrip'); p.add_argument('image', type=Path); p.set_defaults(fn=cmd_roundtrip)
    p = sub.add_parser('syx'); p.add_argument('action', choices=('encode', 'decode', 'inspect'))
    p.add_argument('input', type=Path); p.add_argument('output', type=Path, nargs='?')
    p.add_argument('--base', type=Path); p.set_defaults(fn=cmd_syx)
    a = ap.parse_args(argv)
    if a.command == 'syx' and a.action != 'inspect' and a.output is None:
        ap.error('syx encode/decode need an output path')
    try:
        return a.fn(a) or 0
    except (OSError, ValueError, EOFError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
