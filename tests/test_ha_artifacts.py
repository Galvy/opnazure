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
        self.assertIn('ha/agent.py', sources)
        self.assertIn('ha/install.py', sources)
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
                     'test -s "$OPNAZURE_SOURCE/ha/agent.py"\ntest -s "$OPNAZURE_SOURCE/config.xml"\n'
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
                        publicIPAddress='203.0.113.10', ha={'node': 'Primary'})
        self.assertEqual(prepare_config.config_filename(settings), 'config.xml')
        tree = ET.fromstring(prepare_config.render_config((ROOT / 'scripts/config.xml').read_text(), settings))
        self.assertIsNone(tree.find('hasync'))
        self.assertEqual(tree.findtext('system/dnsserver'), '168.63.129.16')
        self.assertIsNotNone(tree.find('system/dnslocalhost'))
        self.assertEqual(len(tree.findall('staticroutes/route')), 0)
        probes = [r for r in tree.findall('filter/rule') if r.findtext('descr') == 'Azure HA role probe']
        self.assertEqual(len(probes), 2)
        self.assertEqual({r.findtext('interface') for r in probes}, {'wan', 'lan'})

    def test_fencing_permissions_do_not_include_vm_start_or_broad_contributor(self):
        document = json.loads((ROOT / 'ARM/active-backup.json').read_text())
        actions = [a for obj in objects(document) for a in obj.get('actions', [])]
        self.assertIn('Microsoft.Compute/virtualMachines/powerOff/action', actions)
        self.assertNotIn('Microsoft.Compute/virtualMachines/start/action', actions)
        self.assertFalse(any(a.endswith('/*') or a == '*' for a in actions))

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
