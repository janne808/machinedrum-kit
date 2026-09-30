#!/usr/bin/env python3
"""Prepare the emulator's third-party sources: check the submodule pins,
initialize only the nested submodules the build needs, and apply the kit's
patches (idempotently).

  python3 setup.py            # init + patch (safe to repeat)
  python3 setup.py --check    # exit 1 unless pins and patches are exactly in place
  python3 setup.py --reset    # discard the patches (git checkout in the submodules)

See docs/18-emulator.md for what each patch does.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEAR = HERE / 'third_party' / 'gearmulator'
PINS = {                                       # repository path -> required commit
    GEAR: 'dfe1e61b0f574f2ebacb7fb497f65e2b0ecdaa02',                        # joelanders/gearmulator-md-mm
    GEAR / 'source/dsp56300': 'a8ff3425f89cb362170c6abe8b1304f3b87bd535',     # joelanders/dsp56300-md-mm
    GEAR / 'source/mc68k': 'ace95b3d0a5a332db147244762dda65f9a010b9f',        # joelanders/mc68k-md-mm
    GEAR / 'source/dsp56300/source/asmjit': '3577608cab0bc509f856ebf6e41b2f9d9f71acc4',  # dsp56300/asmjit
}
NESTED = [(GEAR, ['source/dsp56300', 'source/mc68k']),     # not JUCE/RmlUi/freetype/...: not needed
          (GEAR / 'source/dsp56300', ['source/asmjit'])]
PATCHES = [                                    # applied in order, each inside its own repository
    (GEAR, 'gearmulator-0001-monitor-hooks.patch'),
    (GEAR, 'gearmulator-0002-md-scheduler-30us-quantum.patch'),
    (GEAR / 'source/mc68k', 'mc68k-0001-coldfire-instruction-hook.patch'),
    (GEAR / 'source/dsp56300', 'dsp56300-0001-debug-hooks.patch'),
]


def git(repo, *args, check=True):
    r = subprocess.run(['git', '-C', str(repo), *args], text=True, capture_output=True)
    if check and r.returncode:
        raise SystemExit(f'git {" ".join(args)} in {repo} failed:\n{r.stderr}')
    return r


def head(repo):
    return git(repo, 'rev-parse', 'HEAD').stdout.strip() if (repo / '.git').exists() else None


def patch_state(repo, patch):
    p = str(HERE / 'patches' / patch)
    if git(repo, 'apply', '--reverse', '--check', p, check=False).returncode == 0:
        return 'applied'
    if git(repo, 'apply', '--check', p, check=False).returncode == 0:
        return 'clean'
    return 'conflict'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--reset', action='store_true')
    a = ap.parse_args()

    if not (GEAR / '.git').exists():
        if a.check:
            raise SystemExit('gearmulator submodule missing: git submodule update --init tools/emulator/third_party/gearmulator')
        subprocess.run(['git', '-C', str(HERE), 'submodule', 'update', '--init', str(GEAR)], check=True)
    if not a.check:
        for repo, paths in NESTED:
            git(repo, 'submodule', 'update', '--init', *paths)
    for repo, commit in PINS.items():
        actual = head(repo)
        if actual != commit:
            raise SystemExit(f'{repo.relative_to(HERE)} is at {actual}, expected {commit}')

    if a.reset:
        for repo, _ in reversed(PATCHES):
            git(repo, 'checkout', '--', '.')
        print('patches removed')
        return
    problems = []
    for repo, patch in PATCHES:
        state = patch_state(repo, patch)
        if state == 'applied':
            continue
        if state == 'clean' and not a.check:
            git(repo, 'apply', str(HERE / 'patches' / patch))
            print(f'applied {patch}')
            continue
        problems.append(f'{patch}: ' + ('not applied (run setup.py)' if state == 'clean'
                                        else 'does not apply (run setup.py --reset, then setup.py)'))
    if problems:
        raise SystemExit('third-party sources are not ready:\n  ' + '\n  '.join(problems))
    print('third-party sources pinned and patched')


if __name__ == '__main__':
    main()
