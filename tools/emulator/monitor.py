#!/usr/bin/env python3
"""Interactive, script and JSONL front end to md-monitor, the native
three-processor monitor (see docs/18-emulator.md).

  python3 monitor.py --firmware IMAGE.bin [--experimental-firmware]
  python3 monitor.py --firmware IMAGE.bin --script commands.txt
  python3 monitor.py --firmware IMAGE.bin --json   < requests.jsonl

From Python:
  from monitor import MonitorClient
  with MonitorClient('image.bin', experimental_firmware=True) as m:
      m.execute('boot'); m.execute('continue 330750')
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))          # tools/: mdkit
HELP = '''cpu coldfire|dsp1|dsp2     Select processor
regs [CPU]                  Registers; status includes all three processors
status                      Execution state, stop reason and readiness
boot [FRAME_LIMIT]          Run to firmware MIDI/panel readiness
break ADDRESS|SYMBOL        Pre-instruction breakpoint on selected CPU
watch r|w|rw SPACE ADDR [N]  Access watchpoint (stop at next same-CPU boundary)
points / delete ID          List/remove breakpoints and watchpoints
step [N]                    N instruction boundaries on selected processor
continue [FRAME_LIMIT]      Run until stop/budget (returns running after 60s)
run [FRAME_LIMIT]           Start asynchronously
pause [WAIT_MS]             Request whole-machine pause
wait [WAIT_MS]              Wait for a stop without requesting one
memory SPACE ADDRESS COUNT Non-destructive memory inspection
 disasm [ADDRESS] [COUNT]    Live disassembly (GNU ColdFire / DSP core)
 dump SPACE ADDR COUNT FILE Write memory bytes (DSP words: little-endian 24-bit)
peripherals                 Non-destructive UART/HI08/DMA state snapshots
trace off|io|all|program|cpu|clear  Configure bounded event ring (8192 events)
trace save FILE             Save retained events as JSONL
events [N]                  Last N trace events
symbols [load FILE]         Symbol list or CPU/SPACE/ADDRESS/NAME/PROVENANCE TSV
symbol SPACE ADDRESS NAME  Add a session symbol
workaround on|off           Control firmware task-list workaround
midi-out                    Drain decoded outbound MIDI messages
midi BYTE...                Queue raw MIDI message; bytes use 0xNN notation
panel ROW MASK              Queue raw panel event (release with mask 0)
lcd FILE                    Save current display as PBM
record FILE FRAMES [2|6]    Render PCM16 WAV (44100 Hz, up to 30 seconds)
input tone HZ LEVEL A|B|both Inject a sine into codec ADC inputs during record
input off                   Disable injected codec input
audio-budget reset|status|off  Measure voice cycles and DAC-buffer overlap
pclog add CPU PC...|sample CPU N|clear|reset|status|dump FILE  Timestamp chosen PCs or every Nth instruction (both DSP cycle counters, DSP1 DMA0/4 DDR)
quit                        End session

SPACE is B for ColdFire bytes, or P/X/Y for DSP words. Numbers are decimal
unless prefixed with 0x. Symbols from RE notes are hypotheses, not verified
function boundaries. Ctrl-C requests a pause. No destructive MMIO read command.
'''


class MonitorError(RuntimeError):
    pass


class MonitorClient:
    """One request/response channel; use run/pause/wait for asynchronous control.

    Do not call concurrently from multiple threads. The native worker is the
    sole emulation owner; queries that inspect it are accepted only when paused.
    """
    def __init__(self, firmware=None, log=None, timeout=90, load_symbols=True, experimental_firmware=False,
                 executable=None):
        firmware = Path(firmware or os.environ.get('MD_FIRMWARE', '')).resolve()
        if not firmware.is_file():
            raise ValueError('Give the firmware image (argument, --firmware or MD_FIRMWARE)')
        self.firmware_sha256 = hashlib.sha256(firmware.read_bytes()).hexdigest()
        if experimental_firmware:
            from mdkit.image import extract
            extract(firmware.read_bytes())  # blob bounds, checksums and DSP records, before boot
        self.timeout = timeout
        self.log = None
        if log:
            self.log = Path(log).open('w')
        env = {k:v for k,v in os.environ.items() if not k.startswith(('GEARMULATOR_', 'MD_MONITOR_'))}
        try:
            self.process = subprocess.Popen([str(executable or ROOT/'build/md-monitor'), str(firmware)] + (['--experimental-firmware'] if experimental_firmware else []),
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.log or subprocess.DEVNULL,
                                            text=True, bufsize=1, start_new_session=True, env=env)
        except OSError:
            if self.log:
                self.log.close()
            raise
        try:
            self.execute('status')
            if load_symbols:
                self.execute('symbols load '+json.dumps(str(ROOT/'symbols/os163.tsv')))
        except BaseException:
            self.close()
            raise

    def execute(self, command, timeout=None):
        if '\n' in command or '\r' in command:
            raise ValueError('One command per request')
        if self.process.poll() is not None:
            raise MonitorError(f'Monitor exited with {self.process.returncode}')
        self.process.stdin.write(command+'\n')
        self.process.stdin.flush()
        return self._receive(self.timeout if timeout is None else timeout)

    def _receive(self, timeout):
        if not select.select([self.process.stdout], [], [], timeout)[0]:
            # A missed response destroys request ordering. Retire this client;
            # callers cannot accidentally read a stale answer as a new result.
            self.process.kill()
            self.process.wait()
            raise TimeoutError('Monitor response deadline exceeded; session terminated')
        line = self.process.stdout.readline()
        if not line:
            raise MonitorError('Monitor closed its protocol stream; inspect its log')
        response = json.loads(line)
        if not response['ok']:
            raise MonitorError(response['error'])
        return response['result']

    def interrupt(self):
        """Request pause without consuming the pending command's response."""
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('quit\n')
                self.process.stdin.flush()
                self.process.wait(timeout=5)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                try:
                    stream.close()
                except BrokenPipeError:
                    # A rejected ROM can exit before buffered quit data is flushed.
                    # Preserve the original monitor error and still close other streams.
                    pass
        if self.log:
            self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def pretty(result):
    if isinstance(result, dict) and 'paused' in result:
        if not result['paused']:
            return 'Running. Use pause or wait.'
        processors = ', '.join(f"{p['cpu']} PC=0x{p['pc']:06x}" for p in result['processors'])
        return f"Stopped: {result['reason']} ({result['stopped_cpu']}), frame {result['frames']}\n{processors}"
    if isinstance(result, list) and result and 'text' in result[0]:
        return '\n'.join(f"0x{r['address']:06x}  {r['text']}"+(f"  <{r['symbol']}>" if r['symbol'] else '') for r in result)
    return json.dumps(result, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path)
    parser.add_argument('--experimental-firmware', action='store_true', help='Allow a noncanonical image after structural/checksum validation')
    parser.add_argument('--json', action='store_true', help='JSONL requests {id, command}, JSONL responses')
    parser.add_argument('--script', type=Path, help='execute a file of monitor commands, exit nonzero on any error')
    parser.add_argument('--log', type=Path, default=Path('monitor.log'))
    args = parser.parse_args()
    args.log.resolve().parent.mkdir(parents=True, exist_ok=True)
    with MonitorClient(args.firmware, args.log, experimental_firmware=args.experimental_firmware) as client:
        if args.script:
            for line in args.script.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                if line.strip()=='quit':
                    break
                result=client.execute(line)
                print(json.dumps({'command':line,'result':result}))
            return 0
        if args.json:
            for line in sys.stdin:
                request={}
                try:
                    request=json.loads(line)
                    if not isinstance(request,dict) or not isinstance(request.get('command'),str):
                        raise ValueError('Expected object with string command')
                    command=request['command']
                    result=client.execute(command)
                    response={'id':request.get('id'), 'ok':True, 'result':result}
                except (ValueError, MonitorError) as error:
                    response={'id':request.get('id') if isinstance(request,dict) else None,'ok':False,'error':str(error)}
                print(json.dumps(response),flush=True)
                if isinstance(request,dict) and request.get('command')=='quit':
                    break
            return 0
        print('Machinedrum monitor. Type help for commands; quit to exit.')
        print(pretty(client.execute('status')))
        while True:
            try:
                line=input('md> ').strip()
                if line in ('quit','exit'):
                    break
                if line in ('help','?'):
                    print(HELP)
                elif line:
                    try:
                        print(pretty(client.execute(line)))
                    except KeyboardInterrupt:
                        client.interrupt()
                        # Consume the original request before sending another.
                        print(pretty(client._receive(30)))
            except KeyboardInterrupt:
                print(pretty(client.execute('pause')))
            except EOFError:
                break
            except (ValueError, MonitorError) as error:
                print('Error:',error)
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (OSError, MonitorError, TimeoutError) as error:
        print('Error:',error,file=sys.stderr)
        raise SystemExit(1)
