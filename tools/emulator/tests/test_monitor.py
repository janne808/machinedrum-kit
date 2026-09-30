"""Monitor integration tests. They boot the real firmware, so they run only when
MD_FIRMWARE points at the stock OS 1.63 image and md-monitor is built:

  MD_FIRMWARE=elektron_sps1-1uw_os1.63.bin python3 -m unittest discover -s tests -v
"""
import json
import hashlib
import shutil
import subprocess
import sys
import os
from pathlib import Path
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import MonitorClient, MonitorError, ROOT  # noqa: E402
from mdkit.image import extract  # noqa: E402

FIRMWARE = Path(os.environ.get('MD_FIRMWARE', ''))


@unittest.skipUnless(FIRMWARE.is_file() and (ROOT / 'build' / 'md-monitor').exists(),
                     'set MD_FIRMWARE to the stock image and build md-monitor')
class MonitorIntegration(unittest.TestCase):
    def test_experimental_image_is_opt_in_and_never_falls_back(self):
        original=FIRMWARE.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            image=Path(temp)/'experimental.bin'
            changed=bytearray(original);changed[0xff010]^=1;image.write_bytes(changed)
            with self.assertRaisesRegex(MonitorError,'fingerprint'):
                MonitorClient(firmware=image,load_symbols=False)
            with MonitorClient(firmware=image,load_symbols=False,experimental_firmware=True) as m:
                self.assertEqual(m.execute('memory B 0xff010 1')['values'],[changed[0xff010]])
                self.assertEqual(m.execute('status')['reason'],'reset')
            # Experimental opt-in must not bypass the independent blob checksum check.
            changed[0x4007]^=1;image.write_bytes(changed)
            with self.assertRaisesRegex(ValueError,'checksum'):
                MonitorClient(firmware=image,experimental_firmware=True)

    def test_coldfire_watchpoint_trace_and_json_protocol(self):
        with tempfile.TemporaryDirectory() as temp, MonitorClient(FIRMWARE) as m:
            point=m.execute('watch w B 0x200000 256')['id']
            hit=m.execute('continue 120000')
            self.assertEqual(hit['reason'],'watchpoint')
            self.assertEqual(hit['stopped_cpu'],'coldfire')
            self.assertEqual(hit['access']['address'],0x200000)
            self.assertEqual(hit['access']['value'],m.execute('memory B 0x200000 1')['values'][0])
            m.execute(f'delete {point}')
            m.execute('trace all');m.execute('step 8000')
            trace=m.execute('trace off')
            self.assertEqual(trace['retained'],8192)
            self.assertGreater(trace['dropped'],0)
            events=m.execute('events 8192')
            self.assertEqual(len(events),8192)
            self.assertEqual([e['sequence'] for e in events],sorted(e['sequence'] for e in events))
            file=Path(temp)/'trace with spaces.jsonl'
            m.execute('trace save '+json.dumps(str(file)))
            self.assertEqual([json.loads(line) for line in file.read_text().splitlines()],events)
            file=Path(temp)/'prefix.bin'
            m.execute('dump B 0x200000 16 '+json.dumps(str(file)))
            self.assertEqual(file.read_bytes(),bytes(m.execute('memory B 0x200000 16')['values']))
            requests=[{'id':1,'command':'regs coldfire'},['invalid'],{'id':2,'command':'quit'}]
            result=subprocess.run([sys.executable,str(ROOT/'harness.py'),'debug','--firmware',str(FIRMWARE),'--json','--log',str(Path(temp)/'json.log')],
                                  input=''.join(json.dumps(r)+'\n' for r in requests),text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            replies=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(replies[0]['id'],1)
            self.assertEqual(replies[0]['result']['pc'],12)
            self.assertFalse(replies[1]['ok'])
            self.assertEqual(replies[2]['id'],2)

    def test_three_processor_debugging_and_live_firmware(self):
        artifact_dir=Path(os.environ['MD_MONITOR_ARTIFACTS']) if os.environ.get('MD_MONITOR_ARTIFACTS') else None
        report={'passed':False,'started_unix':time.time()}
        if artifact_dir:
            artifact_dir.mkdir(parents=True,exist_ok=True)
            (artifact_dir/'report.json').write_text(json.dumps(report)+'\n')
        started=time.monotonic()
        with tempfile.TemporaryDirectory() as temp, MonitorClient(FIRMWARE, log=Path(temp)/'monitor.log') as m:
            status=m.execute('status')
            self.assertEqual(status['reason'],'reset')
            self.assertEqual(status['processors'][0]['pc'],12)
            self.assertTrue(status['processors'][0]['at_instruction_boundary'])
            before=m.execute('peripherals')
            for command in ['memory B 0x300140 1','memory B 0xffffffff 2','step 0',
                            'break 13','cpu nonsense','watch x B 0 1','disasm 13 1']:
                with self.assertRaises(MonitorError,msg=command):m.execute(command)
            listing=m.execute('disasm 12 9')
            self.assertEqual([r['address'] for r in listing[:5]],[12,16,18,22,28])
            self.assertIn('%sr',listing[0]['text'])
            self.assertTrue(any('%acr0' in r['text'] for r in listing))
            time.sleep(.02)
            self.assertEqual(m.execute('status'),status)
            self.assertEqual(m.execute('peripherals'),before)
            stepped=m.execute('step')
            self.assertEqual(stepped['processors'][0]['pc'],16)
            self.assertEqual(stepped['processors'][0]['sr'],0x2700)
            m.execute('cpu dsp1');bp1=m.execute('break 0x100')['id']      # stage-1 bootstrap stub
            m.execute('cpu dsp2');bp2=m.execute('break 0x100')['id']
            hit=m.execute('continue 120000')
            self.assertEqual(hit['reason'],'breakpoint')
            self.assertEqual(hit['stopped_cpu'],'dsp1')
            self.assertEqual(hit['processors'][1]['pc'],0x100)
            m.execute(f'delete {bp1}')
            m.execute('cpu dsp1');first_step=m.execute('step')
            self.assertEqual(first_step['reason'],'step')
            self.assertEqual(first_step['stopped_cpu'],'dsp1')
            m.execute('cpu dsp2');hit=m.execute('continue 120000')
            self.assertEqual(hit['reason'],'breakpoint')
            self.assertEqual(hit['stopped_cpu'],'dsp2')
            self.assertEqual(hit['processors'][2]['pc'],0x100)
            m.execute(f'delete {bp2}')
            m.execute('trace all')
            watch=m.execute('watch w X 0xffff80 128')['id']
            hit=m.execute('continue 120000')
            self.assertEqual(hit['reason'],'watchpoint')
            self.assertEqual(hit['stopped_cpu'],'dsp2')
            self.assertEqual(hit['access']['address'],0xfffffd)
            self.assertEqual(hit['access']['pc'],0x100)
            self.assertEqual(hit['processors'][2]['pc'],0x102)
            self.assertEqual(m.execute('memory P 0x100 1')['values'],[0x08f4bd])
            m.execute('trace save '+json.dumps(str(Path(temp)/'bootstrap-trace.jsonl')))
            state=m.execute('status');events=m.execute('events 8192')
            m.execute('disasm 0x100 4');m.execute('peripherals')
            self.assertEqual(m.execute('events 8192'),events)
            self.assertEqual(m.execute('status'),state)
            m.execute(f'delete {watch}')
            count=state['processors'][2]['observed_instructions']
            stepped=m.execute('step')
            self.assertEqual(stepped['reason'],'step')
            self.assertEqual(stepped['processors'][2]['observed_instructions'],count+1)
            m.execute('trace off')
            m.execute('cpu coldfire')
            boot=m.execute('boot 150000')
            if not boot['paused']:boot=m.execute('wait 60000')
            self.assertEqual(boot.get('reason'),'boot_ready')
            self.assertTrue(boot['dsp_ready'] and boot['midi_ready'])
            self.assertGreater(boot['task_workaround_calls'],0)
            # The reset firmware performs its own decompression; compare with mdkit's.
            mainos=extract(FIRMWARE.read_bytes())[0]['MainOS']
            self.assertEqual(bytes(m.execute('memory B 0x200000 256')['values']),mainos[:256])
            m.execute('run 132300')
            with self.assertRaises(MonitorError):m.execute('regs')
            settled=m.execute('wait 60000')
            self.assertEqual(settled.get('reason'),'frame_budget')
            m.execute('cpu dsp2')
            literal = m.execute('disasm 0x101abf 2')
            self.assertEqual(literal[0]['words'], [0x44f400, 0x200])
            self.assertEqual(literal[0]['size'], 2)
            self.assertFalse(literal[0]['branch'])
            self.assertEqual(literal[1]['address'], 0x101ac1)
            branch = m.execute('disasm 0x10009d 1')[0]
            self.assertEqual(branch['target'], 0x143)
            self.assertTrue(branch['branch'])
            self.assertFalse(branch['call'])
            indirect = m.execute('disasm 0x8d 1')[0]
            self.assertTrue(indirect['dynamic_target'] and indirect['call'])
            self.assertNotIn('target', indirect)
            m.execute('trace clear');m.execute('trace cpu');m.execute('step 32')
            events = m.execute('events 8192')
            self.assertTrue(events)
            self.assertTrue(all(e['cpu'] == 'dsp2' for e in events))
            self.assertTrue(any(e['kind'] == 'instruction' for e in events))
            self.assertEqual(m.execute('trace off')['dropped'], 0)
            m.execute('cpu coldfire')
            m.execute('midi-out')
            m.execute('midi 0xf0 0 0x20 0x3c 2 0 0x70 2 0xf7')
            reply=[]
            for _ in range(20):
                m.execute('continue 2048')
                reply.extend(e['sysex'] for e in m.execute('midi-out'))
                if any(len(r)==10 and r[:8]==[240,0,32,60,2,0,114,2] and r[-1]==247 for r in reply):break
            self.assertTrue(any(len(r)==10 and r[:8]==[240,0,32,60,2,0,114,2] and r[-1]==247 for r in reply),reply)
            lcd=Path(temp)/'lcd.pbm'
            m.execute('lcd '+json.dumps(str(lcd)));before=lcd.read_bytes()
            m.execute('panel 0x22 1');m.execute('continue 2048')
            m.execute('panel 0x22 0');m.execute('continue 22050')
            m.execute('lcd '+json.dumps(str(lcd)));self.assertTrue(lcd.read_bytes()!=before,'Tempo must change the published LCD')
            quiet=m.execute('record '+json.dumps(str(Path(temp)/'quiet.wav'))+' 44100')
            self.assertEqual(quiet['reason'],'audio_complete')
            m.execute('midi 0x90 36 120')
            before_invalid_record=m.execute('status')['audio']
            with self.assertRaises(MonitorError):m.execute('record ignored.wav 32 3')
            self.assertEqual(m.execute('status')['audio'],before_invalid_record)
            for command in ('input tone nan 0.1 A', 'input tone 440 2 A', 'input tone 440 0.1 C'):
                with self.assertRaises(MonitorError):m.execute(command)
            audio=m.execute('record '+json.dumps(str(Path(temp)/'drum.wav'))+' 44100 6')
            self.assertEqual(audio['reason'],'audio_complete')
            self.assertEqual(audio['audio']['frames'],44100)
            self.assertGreater(audio['audio']['peak'],max(.001,quiet['audio']['peak']+.001))
            self.assertGreater(audio['audio']['rms'],.0001)
            import wave
            with wave.open(str(Path(temp)/'drum.wav')) as wav:
                self.assertEqual((wav.getnchannels(),wav.getframerate(),wav.getnframes()),(6,44100,44100))
            with wave.open(str(Path(temp)/'quiet.wav')) as wav:
                self.assertEqual(wav.getnchannels(),2)
            m.execute('midi 0x80 36 0')
            m.execute('workaround off');self.assertFalse(m.execute('status')['task_workaround_enabled'])
            m.execute('workaround on')
            m.execute('run 44100');paused=m.execute('pause')
            self.assertTrue(paused['paused'])
            self.assertEqual(paused['reason'],'pause')
            frozen=m.execute('status');time.sleep(.02);self.assertEqual(m.execute('status'),frozen)
            self.assertTrue(paused['paused'])

    def test_symbols_audio_budget_and_pclog(self):
        with tempfile.TemporaryDirectory() as temp, MonitorClient(FIRMWARE) as m:
            names={s['name'] for s in m.execute('symbols')}
            self.assertTrue({'producer_pass','call_render','block_start'} <= names)
            boot=m.execute('boot 150000')
            if not boot['paused']:boot=m.execute('wait 60000')
            self.assertEqual(boot.get('reason'),'boot_ready')
            m.execute('continue 132300')
            m.execute('midi 0x90 36 120')
            m.execute('cpu dsp2');bp=m.execute('break call_render')['id']
            hit=m.execute('continue 44100');m.execute(f'delete {bp}')
            self.assertEqual(hit['reason'],'breakpoint')
            regs=m.execute('regs dsp2')
            self.assertIn(regs['r6'], range(0x800,0xc00,0x40))
            m.execute('audio-budget reset');m.execute('pclog add dsp1 0x4f');m.execute('pclog add dsp2 0xb4 0xb5')
            m.execute('continue 44100')
            budget=m.execute('audio-budget status');m.execute('audio-budget off')
            self.assertEqual(budget['stale_dac_reads'],0)
            self.assertEqual(budget['writes_to_active_dac_half'],0)
            self.assertLess(budget['mixer_period_max'],74500)
            self.assertGreater(budget['mixer_blocks'],1300)
            log=Path(temp)/'pc.log';m.execute('pclog dump '+json.dumps(str(log)))
            rows=[list(map(int,l.split())) for l in log.read_text().splitlines()]
            self.assertTrue(any(r[0]==1 and r[1]==0x4f for r in rows) and any(r[0]==2 and r[1]==0xb5 for r in rows))
            self.assertEqual(len(rows[0]),8)


class MonitorCleanup(unittest.TestCase):
    def test_dead_child_pipe_does_not_mask_original_error_or_leak_stdout(self):
        from unittest.mock import Mock
        client = MonitorClient.__new__(MonitorClient)
        client.process = Mock()
        client.process.poll.return_value = 1
        client.process.stdin.close.side_effect = BrokenPipeError()
        client.log = Mock()
        client.close()
        client.process.stdout.close.assert_called_once()
        client.log.close.assert_called_once()
