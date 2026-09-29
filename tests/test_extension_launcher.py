"""Reproduce the old extension's rU failure and exercise its inline-command path.
The legacy function excerpts run on modern Python; the compiled launcher runs in
/bin/sh with fetch mocked. This does not simulate an Azure agent or FreeBSD boot.
"""
import base64
import codecs
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def extensions(template):
    for resource in template.get('resources', []):
        if resource['type'] == 'Microsoft.Compute/virtualMachines/extensions':
            yield resource, template
        properties = resource.get('properties', {})
        child = properties.get('template') if isinstance(properties, dict) else None
        if child:
            yield from extensions(child)


class ExtensionLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.extensions = list(extensions(json.loads((ROOT/'ARM/main.json').read_text())))
        cls.extension, cls.template = cls.extensions[-1]
        cls.launcher = cls.template['variables']['bootstrapLauncher']
        expression = cls.extension['properties']['settings']['commandToExecute']
        match = re.match(r"\[format\('((?:[^']|'')*)', variables\('bootstrapLauncher'\),", expression)
        assert match, expression
        cls.command_format = match.group(1).replace("''", "'")
        cls.legacy = dict(open=codecs.open, os=os, shutil=shutil,
                          waagent=types.SimpleNamespace(AddExtensionEvent=Mock()),
                          get_command_to_execute=lambda h: h.get_public_settings()['commandToExecute'],
                          ExtensionShortName='CustomScriptForLinux', DownloadOp='Download',
                          download_external_files=Mock(), download_blobs=Mock())
        exec((ROOT/'tests/fixtures/customscript_preprocessing.py').read_text(), cls.legacy)

    def test_legacy_preprocessing_reproduces_reported_failure(self):
        with tempfile.TemporaryDirectory() as work:
            script=Path(work)/'configureopnsense.sh'
            script.write_text('#!/bin/sh\necho test\n')
            with self.assertRaisesRegex(ValueError, "invalid mode: 'rU'"):
                self.legacy['preprocess_files'](str(script), Mock())

    def test_both_scenarios_skip_legacy_download_and_preprocessing(self):
        self.assertEqual(len(self.extensions), 2)  # AA's loop template and TwoNics
        for extension, template in self.extensions:
            settings=extension['properties']['settings']
            self.assertEqual(settings['fileUris'], [])
            self.assertEqual(template['variables']['bootstrapLauncher'], self.launcher)
            hutil=Mock()
            hutil.get_public_settings.return_value=settings
            hutil.get_protected_settings.return_value={}
            for name in ['download_external_files','download_blobs']:
                self.legacy[name].reset_mock()
            # The old handler returns without preprocessing; daemon then runs commandToExecute.
            self.legacy['download_files'](hutil)
            self.legacy['download_external_files'].assert_not_called()
            self.legacy['download_blobs'].assert_not_called()

    def test_inline_launcher_is_current_safe_to_quote_and_shell_files_are_lf(self):
        self.assertEqual(self.launcher, (ROOT/'scripts/launch-bootstrap.sh').read_text())
        self.assertNotIn("'", self.launcher)
        for path in (ROOT/'scripts').glob('*.sh'):
            self.assertNotIn(b'\r', path.read_bytes(), str(path))

    def run_launcher(self, fetch_mode='success', bootstrap_rc=0, uri='https://example.invalid/configureopnsense.sh', encoded_uri=None):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)
            fake_fetch=work/'fetch'
            fake_fetch.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['LAUNCH_TEST'])
with (root/'fetches').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
out=pathlib.Path(sys.argv[sys.argv.index('-o')+1])
mode=os.environ['FETCH_MODE']
if mode=='failure':
    out.write_text('partial download')
    sys.exit(17)
if mode=='empty': out.write_text('')
else: out.write_text('#!/bin/sh\\nprintf %s "$1" > "$LAUNCH_TEST/result"\\nexit "$BOOTSTRAP_RC"\\n')
''')
            fake_fetch.chmod(0o755)
            (work/'sleep').write_text('#!/bin/sh\nexit 0\n')
            (work/'sleep').chmod(0o755)
            encoded_settings=base64.b64encode(json.dumps(dict(role='TwoNics',windowsSubnet='',scriptURI='https://example.invalid/')).encode()).decode()
            url_argument=encoded_uri if encoded_uri is not None else base64.b64encode(uri.encode()).decode()
            command=self.command_format.format(self.launcher, encoded_settings, url_argument)
            # Matches CustomScript ScriptUtil.parse_args followed by subprocess.Popen(args).
            args=shlex.split(command)
            self.assertEqual(args[:2],['/bin/sh','-c'])
            env=dict(os.environ,PATH=str(work)+os.pathsep+os.environ['PATH'],LAUNCH_TEST=str(work),FETCH_MODE=fetch_mode,BOOTSTRAP_RC=str(bootstrap_rc))
            result=subprocess.run(args,env=env,capture_output=True,text=True)
            fetches=[json.loads(line) for line in (work/'fetches').read_text().splitlines()] if (work/'fetches').exists() else []
            payload=(work/'result').read_text() if (work/'result').exists() else None
            for fetch in fetches:
                target=Path(fetch[fetch.index('-o')+1])
                self.assertFalse(target.parent.exists(), 'Temporary download directory must be cleaned')
            return result,fetches,payload,encoded_settings

    def test_compiled_command_downloads_and_preserves_settings(self):
        result,fetches,payload,settings=self.run_launcher()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(fetches),1)
        self.assertEqual(payload,settings)

    def test_failed_download_retries_without_running_partial_script(self):
        result,fetches,payload,_=self.run_launcher(fetch_mode='failure')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(fetches),3)
        self.assertIsNone(payload)

    def test_empty_download_never_executes(self):
        result,_,payload,_=self.run_launcher(fetch_mode='empty')
        self.assertNotEqual(result.returncode,0)
        self.assertIsNone(payload)

    def test_bootstrap_failure_reaches_extension_exit_status(self):
        result,_,_,_=self.run_launcher(bootstrap_rc=23)
        self.assertEqual(result.returncode,23)

    def test_invalid_transport_inputs_rejected_before_fetch(self):
        for options in [dict(uri='http://example.invalid/script.sh'),dict(encoded_uri='INVALID!')]:
            with self.subTest(options=options):
                result,fetches,payload,_=self.run_launcher(**options)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(fetches,[])
                self.assertIsNone(payload)

    def test_url_is_data_not_executable_shell_text(self):
        uri='https://example.invalid/$(exit 73); literal with spaces.sh'
        result,fetches,_,_=self.run_launcher(uri=uri)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(fetches[0][-1],uri)


if __name__=='__main__': unittest.main()
