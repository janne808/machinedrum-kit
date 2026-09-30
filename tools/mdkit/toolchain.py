"""Thin wrappers around the external assemblers used by the examples.

DSP:      dsp56300-asm (mborgerson/dsp56300), absolute assembly, -f lod
ColdFire: GNU binutils for m68k (as -m5206e, ld, objcopy, nm)
"""
import subprocess
from pathlib import Path

from .image import ImageError
from .machine import program_from_lod


def _run(cmd):
    r = subprocess.run([str(c) for c in cmd], text=True, capture_output=True)
    if r.returncode:
        raise ImageError(f'{Path(str(cmd[0])).name} failed:\n{r.stderr}')
    return r


def assemble_dsp(asm, source, bank, capacity, workdir, include_dirs=()):
    """Assemble `source` at P:bank (sdk.inc defines SDK_BANK and SDK_CAPACITY).
    Returns (words, [init, update, render], symbols)."""
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    (work / 'sdk.inc').write_text(f'SDK_BANK equ ${bank:x}\nSDK_CAPACITY equ ${capacity:x}\n')
    cmd = [asm, source, '-I', work]
    for d in include_dirs:
        cmd += ['-I', d]
    r = _run(cmd + ['-f', 'lod', '-l', work / (Path(source).stem + '.lst')])
    if 'warning' in r.stderr.lower():
        raise ImageError(f'assembler warnings:\n{r.stderr}')
    (work / (Path(source).stem + '.lod')).write_text(r.stdout)
    return program_from_lod(r.stdout, bank, capacity)


def link_coldfire(sources, address, entry, workdir, prefix='m68k-linux-gnu-',
                  include_dirs=(), defsyms=None, cpu='-m5206e'):
    """Assemble `sources` and link them at CPU `address` (use the CS0 alias for
    flash). Returns (raw code bytes, {symbol: address})."""
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    objects = []
    for src in sources:
        obj = work / (Path(src).stem + '.o')
        cmd = [prefix + 'as', cpu]
        for d in include_dirs:
            cmd += ['-I', d]
        for k, v in (defsyms or {}).items():
            cmd.append(f'--defsym={k}={v:#x}')
        _run(cmd + ['-o', obj, src])
        objects.append(obj)
    elf, binary = work / 'coldfire.elf', work / 'coldfire.bin'
    _run([prefix + 'ld', f'-Ttext={address:#x}', '-e', entry, '-o', elf] + objects)
    _run([prefix + 'objcopy', '-O', 'binary', elf, binary])
    symbols = {}
    for line in _run([prefix + 'nm', elf]).stdout.splitlines():
        f = line.split()
        if len(f) == 3:
            symbols[f[2]] = int(f[0], 16)
    with (work / 'coldfire.lst').open('w') as f:
        subprocess.run([prefix + 'objdump', '-d', str(elf)], stdout=f, check=True)
    return binary.read_bytes(), symbols
