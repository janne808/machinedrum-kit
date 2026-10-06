# Tools

`mdkit` is a small Python package (standard library only, Python 3.10+) that
implements the formats and procedures described in the docs:

| Module | Contents | Docs |
| --- | --- | --- |
| `mdkit.codec` | Blob decompressor and compressor, blob records | [compression](../docs/04-compression.md) |
| `mdkit.image` | Blob chain, extraction with checksum checks, DSP upload streams, guarded MainOS edits, version record, image packing, CS0 low-pointer check | [image](../docs/03-firmware-image.md), [packing](../docs/12-packing-firmware.md) |
| `mdkit.sysex` | OS-update SysEx encode, decode and apply | [SysEx](../docs/05-sysex-updates.md) |
| `mdkit.machine` | Descriptors, ID table, family table and menus, DSP dispatch cells, program upload, E12 removal (the examples' layout), sample-memory reservation, LOD parsing; RAM-machine removal for delay-pool builds only | [catalogue](../docs/07-machine-catalogue.md), [packing](../docs/12-packing-firmware.md) |
| `mdkit.toolchain` | Wrappers for `dsp56300-asm` and the GNU m68k binutils | [DSP programming](../docs/13-dsp-programming.md#toolchain) |

It contains no firmware data. Everything it needs is read from the image you
give it, and it refuses to patch anything but the stock OS 1.63 image where a
patch depends on stock contents.

## Command line

Run from this directory, or add it to `PYTHONPATH`:

```sh
python3 -m mdkit info    elektron_sps1-1uw_os1.63.bin
python3 -m mdkit extract elektron_sps1-1uw_os1.63.bin extracted/
python3 -m mdkit check   custom.bin --stock elektron_sps1-1uw_os1.63.bin
python3 -m mdkit roundtrip elektron_sps1-1uw_os1.63.bin
python3 -m mdkit syx encode custom.bin custom.syx
python3 -m mdkit syx decode custom.syx custom-rebuilt.bin --base elektron_sps1-1uw_os1.63.bin
python3 -m mdkit syx inspect custom.syx
```

| Command | Output |
| --- | --- |
| `info` | The blob chain, version record and non-erased pages after the chain |
| `extract` | Five verified payloads, each DSP section as its own file (`NNN-space-address.bin`, 3-byte little-endian words), and `manifest.json` |
| `check` | Exits non-zero on: a checksum failure; a flash-bank word or MainOS edit pointing into the low flash window (hangs real hardware); a changed boot sector or upper flash; a failed SysEx round trip. Give `--stock` to scan MainOS edits. |
| `roundtrip` | Recompresses every payload with mdkit's encoder and decodes it back |
| `syx` | `syx encode` of the stock image reproduces Elektron's `.syx` byte for byte |

## Library

The shape of a build, taken from the GND-SW example:

```python
from mdkit import image as img, machine as mc, sysex

payloads, _ = img.extract(stock)
main = bytearray(payloads['MainOS'])
dsp = img.DspStream(payloads['DSP2'])

desc = mc.descriptor(main, handler_cpu, 8, 'GND', 'SW', labels, defaults)
menu = mc.menu_list(mc.read_list(main, mc.GND_LIST) + [img.flash_cpu(0xFF000)])
mc.remove_e12_machines(main, dsp)                         # frees 0x103DBA-0x135205
mc.register_id(main, 8, img.flash_cpu(0xFF000), dsp)      # free ID, empty entry, unused DSP type
mc.set_family_list(main, 0, mc.GND_LIST, img.flash_cpu(0xFF100))
mc.set_dispatch(dsp, 9, entries)                          # cells must hold the fallback
mc.add_program(dsp, 0x127000, 0x1000, code, reserved=[mc.E12_POOL])   # refuses overlaps
image, records = img.pack_image(stock, {'MainOS': bytes(main), 'DSP2': dsp.bytes()},
                                {0xFF000: bank})
assert not img.low_window_pointers(image, {0xFF000: bank})
open('custom.syx', 'wb').write(sysex.encode_payload(sysex.image_payload(image)))
```

Custom DSP code needs DSP2 memory that nothing else uses. The examples take
E12's sample data with `remove_e12_machines`: the 16 E12 machines go, and
sample memory, the ROM budget and the RAM machines stay stock. The region
(`E12_CODE_REGION`, `E12_POOL`) holds 17 code banks and a 16-track delay pool.
To keep E12, reserve the top of sample memory instead with
`reserve_sample_memory` and the loader guards in `examples/common/guards.s`;
the ROM budget and RAM recording memory then shrink. `remove_ram_machines` is
only for builds that give RAM recording memory to a large delay pool.

## Tests

```sh
python3 -m unittest discover -s tests              # synthetic images, no firmware needed
MDKIT_FIRMWARE=elektron_sps1-1uw_os1.63.bin \
MDKIT_SYSEX=Elektron_SPS1-1UW_OS1.63.syx \
python3 -m unittest discover -s tests              # adds checks against the real image
```

## Emulator

[`emulator/`](emulator/) builds the headless Machinedrum emulator used for every
emulator result in the kit:

- `md-harness`, a boot/MIDI/panel/audio smoke test;
- `md-monitor`, a three-processor debugger with a Python client and a JSONL
  protocol;
- `md-kernel`, which runs a machine's DSP code outside the firmware.

Gearmulator and its DSP/ColdFire cores are git submodules, pinned and patched
by `emulator/setup.py`. See [docs/18-emulator.md](../docs/18-emulator.md).

```sh
git submodule update --init tools/emulator/third_party/gearmulator
python3 emulator/harness.py build
```

## Examples

Both need `dsp56300-asm` ([mborgerson/dsp56300](https://github.com/mborgerson/dsp56300))
and GNU m68k binutils. Both remove the E12 machines and keep every other stock
machine, with stock sample memory. Both images were
verified in the emulator (registration, front-panel selection, bit-exact
audio); neither has been flashed to hardware.

| Example | What it shows |
| --- | --- |
| [`examples/gnd-sw`](examples/gnd-sw/) | GND-SW, a MIDI-tuned PolyBLEP saw, appended to the stock GND menu. Stock sample budgets, RAM-R1 recording and the RAM family's new slot are checked. |
| [`examples/nfx-gn`](examples/nfx-gn/) | The walkthrough machine from [doc 11](../docs/11-writing-a-custom-machine.md): a neighbour effect in a new family, appended to the relocated family table |
| [`examples/common/guards.s`](examples/common/guards.s) | Sample-loader guards for builds that reserve sample memory instead (not used by the examples) |

Each example has `build.py` (image, `.syx`, `build.json`), `model.py` (bit-exact
integer model) and `verify.py`. The verifier runs kernel tests with
`md-kernel`, then boots the image in the monitor. It needs the emulator
built:

```sh
python3 examples/gnd-sw/build.py  --firmware STOCK.bin --asm dsp56300-asm --output out/sw
python3 examples/gnd-sw/verify.py --build out/sw --firmware STOCK.bin
python3 examples/nfx-gn/build.py  --firmware STOCK.bin --asm dsp56300-asm --output out/gn
python3 examples/nfx-gn/verify.py --build out/gn
```

## External tools

| Tool | Used for |
| --- | --- |
| `dsp56300-asm` from mborgerson/dsp56300 (tested commit `5b96b127`), `-f lod` output | DSP code |
| `m68k-linux-gnu-as -m5206e`, `ld -Ttext=<alias>`, `objcopy -O binary`, `nm` | ColdFire handlers |
| `m68k-linux-gnu-objdump -m m68k:isa-a` | ColdFire listings ([disassembly guide](../docs/15-disassembly-guide.md)) |
