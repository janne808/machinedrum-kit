# Emulator harness

Headless Machinedrum emulator (Gearmulator board model, ColdFire + two
DSP56303) with a three-processor debugger, a smoke test and a kernel runner.
**Full documentation: [docs/18-emulator.md](../../docs/18-emulator.md).**

```sh
git submodule update --init tools/emulator/third_party/gearmulator   # not --recursive
cd tools/emulator
python3 harness.py build                     # pin check, patches, CMake + Ninja
cmake --build build --target check           # no firmware needed
python3 harness.py verify --firmware elektron_sps1-1uw_os1.63.bin --output out/verify
python3 monitor.py --firmware elektron_sps1-1uw_os1.63.bin
```

| Path | Contents |
| --- | --- |
| `third_party/gearmulator` | Git submodule, pinned (with three nested submodules); see `setup.py` |
| `patches/` | The kit's changes to the third-party code, applied by `setup.py` |
| `src/harness.cpp` | `md-harness`: boot, SysEx, panel and audio smoke test |
| `src/monitor.cpp`, `src/coldfire_disasm.h` | `md-monitor`: the debugger |
| `src/kernel.cpp` | `md-kernel`: machine DSP code outside the firmware |
| `src/test_debug_hooks.cpp` | DSP hook regression test |
| `harness.py`, `monitor.py` | Python front ends and client API |
| `symbols/os163.tsv` | Addresses documented by the kit, for breakpoints and disassembly |
| `tests/` | Monitor integration tests (`MD_FIRMWARE=… python3 -m unittest discover -s tests`) |

GPL-3.0, like its dependencies (see `LICENSE`).
