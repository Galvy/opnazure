"""Run the orchestration with mocked FreeBSD commands, never the real bootstrap.
These tests verify failure propagation/reboot ordering, not Azure compatibility.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

MOCK=r'''#!/usr/bin/env python3
import json,os,pathlib,shutil,sys
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
root=pathlib.Path(os.environ['FAKE_ROOT'])
with (root/'events').open('a') as f: f.write(name+' '+' '.join(args)+'\n')
if name == os.environ.get('FAIL_COMMAND'):
    sys.exit(17)
if name=='id': print('0')
elif name=='uname': print('FreeBSD' if '-s' in args else 'amd64')
elif name=='freebsd-version': print('15.1-RELEASE-p3')
elif name=='fetch':
    out=pathlib.Path(args[args.index('-o')+1]); url=args[-1]
    source=pathlib.Path(os.environ['TEST_REPO'])
    if '169.254.169.254' in url: out.write_text('{}')
    elif 'opnsense-bootstrap.sh.in' in url: shutil.copyfile(source/'tests/fixtures/opnsense-bootstrap.sh',out)
    else: shutil.copyfile(source/'scripts'/url.rsplit('/',1)[-1],out)
elif name=='pkg':
    if args[0]=='query':
        print('2.15.0.1' if args[-1]=='azure-agent' else '26.7.4_1')
    elif args[0]=='version': print('=')
elif name=='sh':
    # Never execute the upstream conversion, which would delete packages.
    assert args[0].endswith('/bootstrap.sh')
elif name=='sed':
    # FreeBSD sed -i '' syntax differs from GNU sed. Simulate line deletion.
    path=pathlib.Path(args[-1]); key=args[-2].split('^')[1].split('=')[0]
    path.write_text(''.join(line for line in path.read_text().splitlines(True) if not line.startswith(key+'=')))
elif name in ('sysrc','shutdown','waagent','sha256'): pass
else: raise RuntimeError(name)
'''


class FlowTests(unittest.TestCase):
    def run_flow(self, fail=None, repeat=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            fakeusr=root/'usr/local'
            for sub in ['sbin','bin','etc/rc.syshook.d/start','opnsense/service/conf/actions.d']:
                (fakeusr/sub).mkdir(parents=True,exist_ok=True)
            (root/'var/log').mkdir(parents=True)
            (fakeusr/'etc/waagent.conf.sample').write_text('ResourceDisk.EnableSwap=y\n')
            (fakeusr/'bin/python3').symlink_to(sys.executable)
            mock=root/'mock'; mock.mkdir()
            for command in ['id','uname','freebsd-version','fetch','pkg','sh','sed','sysrc','shutdown','sha256']:
                path=mock/command; path.write_text(MOCK); path.chmod(0o755)
            agent=fakeusr/'sbin/waagent'; agent.write_text(MOCK); agent.chmod(0o755)
            text=(ROOT/'scripts/configureopnsense.sh').read_text()
            text=text.replace('/usr/local/',str(fakeusr)+'/').replace('/var/',str(root/'var')+'/')
            text=text.replace('PATH=/sbin:/bin:/usr/sbin:/usr/bin:',f'PATH={mock}:/sbin:/bin:/usr/sbin:/usr/bin:')
            # Test retries without a real 10 second wait.
            text=text.replace('sleep 5','true')
            script=root/'run.sh'; script.write_text(text)
            settings=dict(scriptURI='https://example.test/scripts/', opnVersion='26.7', agentMinimumVersion='2.15.0.1',role='TwoNics', trustedSubnet='10.0.1.0/24',windowsSubnet='')
            encoded=base64.b64encode(json.dumps(settings).encode()).decode()
            env=dict(os.environ, FAKE_ROOT=str(root),TEST_REPO=str(ROOT),FAIL_COMMAND=fail or '')
            result=subprocess.run(['/bin/sh',str(script),encoded],env=env,capture_output=True,text=True)
            events=(root/'events').read_text()
            status=(root/'var/db/opnazure/status').read_text() if (root/'var/db/opnazure/status').exists() else ''
            log=(root/'var/log/opnazure-bootstrap.log').read_text() if (root/'var/log/opnazure-bootstrap.log').exists() else ''
            if repeat:
                first_events=events
                again=subprocess.run(['/bin/sh',str(script),encoded],env=env,capture_output=True,text=True)
                events=(root/'events').read_text()[len(first_events):]
                return again,events,status,log
            return result,events,status,log

    def test_success_reboots_after_package_checks(self):
        result,events,status,log=self.run_flow()
        self.assertEqual(result.returncode,0,log+result.stderr)
        self.assertEqual(status.strip(),'awaiting-reboot')
        self.assertGreater(events.index('shutdown '),events.index('pkg check '))
        self.assertGreater(events.index('pkg install '),events.index('sh '))

    def test_failures_never_schedule_reboot_or_claim_success(self):
        for command in ['fetch','sh','pkg','waagent']:
            with self.subTest(command=command):
                result,events,status,log=self.run_flow(command)
                self.assertNotEqual(result.returncode,0,log)
                self.assertNotIn('shutdown ',events)
                self.assertTrue(status.startswith('failed:'),status)

    def test_partial_conversion_is_not_repeated(self):
        result,events,status,log=self.run_flow('pkg',repeat=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('sh ',events)
        self.assertNotIn('pkg ',events)
        self.assertIn('Refusing',result.stderr)

    def test_completed_conversion_is_not_repeated(self):
        result,events,status,log=self.run_flow(repeat=True)
        self.assertEqual(result.returncode,0,log)
        self.assertNotIn('sh ',events)
        self.assertNotIn('shutdown ',events)

    def test_shutdown_failure_is_reported(self):
        result,events,status,log=self.run_flow('shutdown')
        self.assertNotEqual(result.returncode,0)
        self.assertTrue(status.startswith('failed:'),status)


if __name__=='__main__': unittest.main()
