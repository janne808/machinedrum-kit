#!/usr/bin/env python3
"""Machinedrum OS 1.63 emulator harness for the machinedrum-kit.

  python3 harness.py setup                      pin + patch third-party sources
  python3 harness.py build [--jobs N]           configure and build (Ninja)
  python3 harness.py verify --firmware IMG --output DIR
                                                boot, MIDI, panel and audio smoke test
  python3 harness.py debug --firmware IMG ...   interactive monitor (see monitor.py -h)

See docs/18-emulator.md.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))          # tools/: mdkit
from mdkit import image as img                # noqa: E402

LIMITATIONS = [
    'The board model periodically updates a firmware task list for panel progress (not pure peripheral emulation).',
    'No DSP external-memory wait states or instruction-cache timing: cycle counts are optimistic.',
    'Behavioural smoke test; no physical-hardware audio or timing comparison.',
    'DSP bootstrap ROM is modelled by the board model\'s host-loader state machine.',
]


def positive(value):
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return n


def cmd_setup(_):
    return subprocess.call([sys.executable, str(ROOT / 'setup.py')])


def cmd_build(a):
    subprocess.run([sys.executable, str(ROOT / 'setup.py')], check=True)
    subprocess.run(['cmake', '-S', str(ROOT), '-B', str(ROOT / 'build'), '-G', 'Ninja'], check=True)
    subprocess.run(['cmake', '--build', str(ROOT / 'build'), '-j', str(a.jobs)], check=True)
    return 0


def cmd_verify(a):
    if a.boot_seconds > 120:
        raise ValueError('--boot-seconds must be at most 120')
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):                  # never mix a new result with an old one
        raise ValueError('verification output must be empty')
    data = a.firmware.read_bytes()
    payloads, extraction = img.extract(data)
    if not extraction['stock'] and not a.experimental_firmware:
        raise ValueError('not the stock OS 1.63 image; add --experimental-firmware for custom images')
    (output / 'mainos.bin').write_bytes(payloads['MainOS'])
    executable = ROOT / 'build' / 'md-harness'
    if not executable.exists():
        raise ValueError('build first: python3 harness.py build')
    report = {'passed': False, 'started_unix': time.time(), 'firmware': extraction,
              'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
              'limitations': LIMITATIONS}
    command = [str(executable), str(a.firmware.resolve()), str(output), str(output / 'mainos.bin'),
               str(a.boot_seconds * 44100)] + (['--experimental-firmware'] if a.experimental_firmware else [])
    report['command'] = command
    env = {k: v for k, v in os.environ.items() if not k.startswith('GEARMULATOR_')}
    started = time.monotonic()
    try:
        with (output / 'emulator.log').open('w') as log:
            r = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=output, env=env, timeout=a.timeout)
        report['returncode'] = r.returncode
        runtime = output / 'runtime.json'
        if runtime.exists():
            report['runtime'] = json.loads(runtime.read_text())
        report['firmware_unchanged'] = hashlib.sha256(a.firmware.read_bytes()).hexdigest() == extraction['sha256']
        report['passed'] = (r.returncode == 0 and report.get('runtime', {}).get('passed', False)
                            and report['firmware_unchanged'])
        if not report['passed']:
            failure = output / 'failure.txt'
            report['error'] = failure.read_text().strip() if failure.exists() else 'see emulator.log'
    except subprocess.TimeoutExpired:
        report['error'] = f'emulator exceeded {a.timeout} s'
    report['elapsed_seconds'] = time.monotonic() - started
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(('PASS' if report['passed'] else 'FAIL') + f': {output / "report.json"}')
    if not report['passed']:
        print(report.get('error', ''), file=sys.stderr)
    return 0 if report['passed'] else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'debug':
        return subprocess.call([sys.executable, str(ROOT / 'monitor.py'), *sys.argv[2:]])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('setup').set_defaults(fn=cmd_setup)
    p = sub.add_parser('build'); p.add_argument('--jobs', type=positive, default=os.cpu_count() or 4)
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser('verify')
    p.add_argument('--firmware', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--experimental-firmware', action='store_true')
    p.add_argument('--timeout', type=positive, default=180, help='wall-clock seconds')
    p.add_argument('--boot-seconds', type=positive, default=20, help='emulated seconds allowed for boot')
    p.set_defaults(fn=cmd_verify)
    sub.add_parser('debug', help='interactive monitor; arguments go to monitor.py')
    a = ap.parse_args()
    return a.fn(a)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f'error: {e}', file=sys.stderr)
        raise SystemExit(1)
