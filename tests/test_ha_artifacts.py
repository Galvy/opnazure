"""Exercise embedded delivery and validate the standalone deployment contract."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_config


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def embedded():
    document = json.loads((ROOT / 'ARM/active-backup.json').read_text())
    template = next(x for x in objects(document) if 'embeddedSources' in x.get('variables', {}))
    variables = template['variables']
    sources = {}
    for name, value in variables['embeddedSources'].items():
        if value.startswith("[variables('"):
            value = variables[value[len("[variables('"):-len("')]")]]
        sources[name] = value
    return template, sources


class ArtifactTests(unittest.TestCase):
    def test_embedded_sources_are_current_and_complete(self):
        template, sources = embedded()
        for name, encoded in sources.items():
            self.assertEqual(base64.b64decode(encoded), (ROOT / 'scripts' / name).read_bytes(), name)
        self.assertNotIn('ha/agent.py', sources)
        self.assertNotIn('ha/install.py', sources)
        self.assertNotIn('config-active-active-primary.xml', sources)
        self.assertLess(len(base64.b64encode(json.dumps(sources).encode())), 120000)
        extensions = [x for x in objects(template) if x.get('type') == 'Microsoft.Compute/virtualMachines/extensions']
        self.assertEqual(len(extensions), 1)
        self.assertEqual(extensions[0]['properties']['settings']['fileUris'], [])

    def test_execute_actual_embedded_launcher_without_network(self):
        _, sources = embedded()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'result'
            # Only substitute the privileged conversion script. Exercise the
            # actual extraction and shell argument handling with all other files.
            script = '#!/bin/sh\nset -eu\nprintf "%s" "$1" > "$HA_TEST_RESULT"\n' \
                     'test -s "$OPNAZURE_SOURCE/prepare_config.py"\ntest -s "$OPNAZURE_SOURCE/config.xml"\n'
            sources['configureopnsense.sh'] = base64.b64encode(script.encode()).decode()
            payload = base64.b64encode(json.dumps(sources).encode()).decode()
            launcher = (ROOT / 'scripts/embedded-bootstrap.sh').read_text()
            self.assertNotIn("'", launcher)
            launcher = launcher.replace('/usr/local/bin/python3 /usr/local/bin/python3.[0-9]*', sys.executable)
            launcher = launcher.replace('/var/tmp/opnazure-source.XXXXXX', tmp + '/source.XXXXXX')
            command = "/bin/sh -c '" + launcher + "' opnazure eyJ0ZXN0Ijp0cnVlfQ== " + payload
            result = subprocess.run(shlex.split(command), env=dict(os.environ, HA_TEST_RESULT=str(target)),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(), 'eyJ0ZXN0Ijp0cnVlfQ==')
            self.assertEqual(list(Path(tmp).glob('source.*')), [])

    def test_ha_configuration_has_no_enterprise_vpn_or_sync(self):
        settings = dict(role='Primary', opnVersion='26.7', agentMinimumVersion='2.15.0.1',
                        scriptURI='https://example.test/scripts/', trustedSubnet='10.80.1.0/24',
                        localTrustedIP='10.80.1.4', peerTrustedIP='10.80.1.5',
                        publicIPAddress='203.0.113.10', ha={'node': 'Primary', 'mode': 'manual'})
        self.assertEqual(prepare_config.config_filename(settings), 'config.xml')
        tree = ET.fromstring(prepare_config.render_config((ROOT / 'scripts/config.xml').read_text(), settings))
        self.assertIsNone(tree.find('hasync'))
        self.assertEqual(tree.findtext('system/dnsserver'), '168.63.129.16')
        self.assertIsNotNone(tree.find('system/dnslocalhost'))
        self.assertEqual(len(tree.findall('staticroutes/route')), 0)
        probes = [r for r in tree.findall('filter/rule') if r.findtext('descr') == 'Azure HA role probe']
        self.assertEqual(probes, [])

    def test_no_automatic_controller_resources_or_permissions(self):
        document = json.loads((ROOT / 'ARM/active-backup.json').read_text())
        types = {obj.get('type', '') for obj in objects(document) if isinstance(obj.get('type', ''), str)}
        for resource_type in types:
            self.assertFalse(resource_type.startswith(('Microsoft.Authorization/',
                             'Microsoft.ManagedIdentity/', 'Microsoft.Storage/')), resource_type)
        self.assertFalse(any('identity' in obj for obj in objects(document)))
        probes = [probe for obj in objects(document)
                  if isinstance(obj.get('probe'), dict)
                  for probe in obj['probe'].get('value', [])]
        self.assertEqual(len(probes), 2)
        for probe in probes:
            self.assertEqual(probe['properties']['protocol'], 'Tcp')
            self.assertEqual(probe['properties']['port'], 443)

    def test_manual_upgrade_does_not_rerun_pinned_firstboot_checks(self):
        source = (ROOT / 'scripts/configureopnsense.sh').read_text()
        hook = source.split("cat > /usr/local/etc/rc.syshook.d/start/95-opnazure-verify <<'HOOK'\n", 1)[1].split('\nHOOK', 1)[0]
        for manual_mode, completed, expected in ((True, True, 0), (False, True, 1), (True, False, 1)):
            with self.subTest(manual=manual_mode, completed=completed), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / 'work'
                work.mkdir()
                (work / 'certificate-renewed').touch()
                if manual_mode:
                    (work / 'manual-maintenance').touch()
                if completed:
                    (work / 'first-boot-complete').touch()
                script = hook.replace('/var/db/opnazure', str(work)).replace(
                    '/var/log/opnazure-firstboot.log', str(Path(tmp) / 'log')).replace(
                    '/usr/local/sbin/opnazure-verify', '/bin/false')
                result = subprocess.run(['/bin/sh'], input=script, text=True, capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_management_deny_precedes_active_workload_gate(self):
        document = json.loads((ROOT / 'ARM/active-backup.json').read_text())
        rules = next(obj['controlRules'] for obj in objects(document) if 'controlRules' in obj)
        deny = next(r for r in rules if r['name'] == 'Deny-Public-Management')['properties']
        allow = next(r for r in rules if r['name'] == 'Management')['properties']
        self.assertLess(allow['priority'], deny['priority'])
        self.assertLess(deny['priority'], 200)
        self.assertEqual(deny['access'], 'Deny')
        self.assertEqual(deny['destinationPortRanges'], ['22', '443'])

if __name__ == '__main__':
    unittest.main()
