"""Single-firewall deployment contract and embedded TwoNics bootstrap regression."""
import base64
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET
from test_twonic import SETTINGS, render_config
from urllib.parse import unquote
from test_ha_artifacts import objects
import test_bootstrap_flow

ROOT = Path(__file__).resolve().parents[1]

class SingleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.arm = json.loads((ROOT / 'ARM/single-opnsense.json').read_text())
        cls.ui = json.loads((ROOT / 'ARM/single-opnsense.uiFormDefinition.json').read_text())

    def test_one_vm_and_no_ha_infrastructure(self):
        resources = [o for o in objects(self.arm) if isinstance(o.get('type'), str)]
        vms = [o for o in resources if o['type'] == 'Microsoft.Compute/virtualMachines']
        self.assertEqual(len(vms), 1)
        self.assertNotIn('copy', vms[0])
        self.assertEqual(len(vms[0]['properties']['networkProfile']['networkInterfaces']), 2)
        forbidden = ('Microsoft.Network/loadBalancers', 'Microsoft.Storage/',
                     'Microsoft.ManagedIdentity/', 'Microsoft.Authorization/')
        self.assertFalse(any(o['type'].startswith(forbidden) for o in resources))
        nic_modules = [o for o in resources if isinstance(o.get('properties'), dict) and 'nicName' in o['properties'].get('parameters', {})]
        self.assertEqual(len(nic_modules), 2)
        wan = next(o for o in nic_modules if 'publicIPId' in o['properties']['parameters'])
        self.assertIn('publicIPId', wan['properties']['parameters'])
        self.assertNotIn('secondaryManagementURL', self.arm['outputs'])
        self.assertIn('trustedNextHop', self.arm['outputs'])

    def test_runtime_is_twonic_without_peer_configuration(self):
        module = next(o for o in self.arm['resources'] if 'ShellScriptObj' in o['properties']['parameters'])
        settings = module['properties']['parameters']['ShellScriptObj']['value']
        self.assertEqual(settings['OpnType'], 'TwoNics')
        self.assertEqual(settings['WindowsSubnetName'], '')
        self.assertNotIn('haConfig', module['properties']['parameters'])
        embedded = next(o['variables'] for o in objects(self.arm) if 'embeddedSources' in o.get('variables', {}))
        for filename, variable in embedded['embeddedSources'].items():
            value = embedded[variable[len("[variables('"):-len("')]")]]
            self.assertEqual(base64.b64decode(value), (ROOT / 'scripts' / filename).read_bytes())

    def test_twonic_does_not_fetch_unbundled_ha_xml(self):
        result, events, status, log = test_bootstrap_flow.FlowTests().run_flow(role='TwoNics')
        self.assertEqual(result.returncode, 0, log + result.stderr)
        self.assertNotIn('config-active-active', events)
        self.assertEqual(status.strip(), 'awaiting-reboot')

    def test_management_port_matches_guest_nsg_and_output(self):
        module = next(o for o in self.arm['resources'] if 'ShellScriptObj' in o['properties']['parameters'])
        port = module['properties']['parameters']['ShellScriptObj']['value']['managementPort']
        self.assertEqual(port, 50443)
        source = (ROOT / 'scripts/config.xml').read_text()
        tree = ET.fromstring(render_config(source, dict(SETTINGS, managementPort=port)))
        self.assertEqual(tree.findtext('system/webgui/port'), '50443')
        wan_ports = [r.findtext('destination/port') for r in tree.findall('filter/rule')
                     if r.findtext('interface') == 'wan' and r.findtext('protocol') == 'tcp']
        self.assertIn('50443', wan_ports)
        self.assertNotIn('443', wan_ports)
        self.assertEqual(tree.findtext('.//sslPorts'),
                         ET.fromstring(source).findtext('.//sslPorts'))
        rules = [o for o in objects(self.arm) if o.get('name') == 'Management-HTTPS']
        self.assertEqual(rules[0]['properties']['destinationPortRange'], '50443')
        self.assertIn(':50443', self.arm['outputs']['managementURL']['value'])
        original = ET.fromstring(render_config(source, SETTINGS))
        self.assertFalse(original.findtext('system/webgui/port'))
        self.assertEqual(original.findtext('filter/rule/destination/port'), '443')

    def test_form_selects_one_vm_and_wires_required_parameters(self):
        self.assertEqual((ROOT/'ARM/single-opnsense.uiFormDefinition.json').read_bytes(),
                         (ROOT/'bicep/single-opnsense.uiFormDefinition.json').read_bytes())
        steps = {s['name']: {e['name']: e for e in s['elements']} for s in self.ui['view']['properties']['steps']}
        selector = steps['virtualMachines']['vmSize']
        self.assertEqual(selector['type'], 'Microsoft.Compute.SizeSelector')
        self.assertEqual(selector['count'], 1)
        params = self.ui['view']['outputs']['parameters']
        self.assertTrue(set(params) <= set(self.arm['parameters']))
        self.assertTrue({k for k,v in self.arm['parameters'].items() if 'defaultValue' not in v} <= set(params))
        for step, field in re.findall(r"steps\('([^']+)'\)\.([A-Za-z][A-Za-z0-9]*)", json.dumps(self.ui)):
            self.assertIn(field, steps[step])
        self.assertNotIn('clusterName', params)
        self.assertEqual(params['virtualMachineName'], "[steps('virtualMachines').virtualMachineName]")

    def test_first_button_is_single_template_and_matching_guided_form(self):
        url = re.search(r'\]\((https://portal.azure.com/[^)]+)\)', (ROOT/'README.md').read_text()).group(1)
        raw = 'https://raw.githubusercontent.com/Galvy/opnazure/feature/single-opnsense-site/ARM/'
        self.assertEqual(unquote(url), 'https://portal.azure.com/#create/Microsoft.Template/uri/' + raw +
                         'single-opnsense.json/uiFormDefinitionUri/' + raw + 'single-opnsense.uiFormDefinition.json')

if __name__ == '__main__':
    unittest.main()
