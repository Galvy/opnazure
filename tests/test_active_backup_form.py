"""Check form/template wiring and user-input validation without deploying Azure."""
import ipaddress
import json
from pathlib import Path
import re
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

class FormTests(unittest.TestCase):
    def setUp(self):
        self.form = json.loads((ROOT / 'ARM/active-backup.uiFormDefinition.json').read_text())
        self.params = json.loads((ROOT / 'ARM/active-backup.json').read_text())['parameters']
        self.steps = {s['name']: {e['name']: e for e in s['elements']}
                      for s in self.form['view']['properties']['steps']}
        self.outputs = self.form['view']['outputs']['parameters']

    def test_outputs_cover_required_parameters_and_reference_existing_fields(self):
        self.assertTrue(set(self.outputs) <= set(self.params))
        required = {k for k, v in self.params.items() if 'defaultValue' not in v}
        self.assertTrue(required <= set(self.outputs))
        serialized = json.dumps(self.form)
        for step, element in re.findall(r"steps\('([^']+)'\)\.([A-Za-z][A-Za-z0-9]*)", serialized):
            self.assertIn(step, self.steps)
            self.assertIn(element, self.steps[step])
        for port in ('openVpnPort', 'wireGuardPort'):
            self.assertEqual(self.params[port]['type'], 'int')
            self.assertEqual(self.outputs[port], f"[int(steps('vpn').{port})]")

    def test_vm_selector_is_scoped_and_counts_both_nodes(self):
        selector = self.steps['virtualMachines']['vmSize']
        self.assertEqual(selector['type'], 'Microsoft.Compute.SizeSelector')
        self.assertEqual(selector['count'], 2)
        self.assertEqual(selector['scope']['location'], "[steps('basics').resourceScope.location.name]")
        self.assertEqual(selector['scope']['subscriptionId'], "[steps('basics').resourceScope.subscription.subscriptionId]")
        self.assertIn(self.params['virtualMachineSize']['defaultValue'], selector['constraints']['allowedSizes'])
        self.assertEqual(self.outputs['virtualMachineSize'], "[steps('virtualMachines').vmSize]")

    def test_password_stays_masked_and_maps_only_to_secure_parameter(self):
        field = self.steps['virtualMachines']['bootstrapAdminPassword']
        self.assertEqual(field['type'], 'Microsoft.Common.PasswordBox')
        self.assertFalse(field['options']['hideConfirmation'])
        self.assertNotIn('defaultValue', field)
        for name, expression in self.outputs.items():
            if 'bootstrapAdminPassword' in expression:
                self.assertEqual(self.params[name]['type'].lower(), 'securestring')
        self.assertEqual(sum('bootstrapAdminPassword' in v for v in self.outputs.values()), 1)

    def test_management_cidr_rejects_invalid_octets_and_masks(self):
        field = self.steps['virtualMachines']['managementSourceCIDR']
        regex = field['constraints']['validations'][0]['regex']
        for value in ('203.0.113.10/32', '198.51.100.0/24'):
            self.assertRegex(value, regex)
        for value in ('999.1.2.3/32', '203.0.113.10/33', '203.0.113.10', '', '1.2.3.4/032'):
            self.assertIsNone(re.fullmatch(regex, value), value)

    def test_port_validation_accepts_only_the_template_range(self):
        regex = self.steps['vpn']['openVpnPort']['constraints']['validations'][0]['regex']
        for port in range(0, 65538):
            self.assertEqual(bool(re.fullmatch(regex, str(port))), 1024 <= port <= 65535, port)
        check = self.steps['vpn']['wireGuardPort']['constraints']['validations'][1]
        self.assertEqual(check['isValid'], "[not(equals(steps('vpn').wireGuardPort, steps('vpn').openVpnPort))]")

    def test_network_defaults_match_template_and_do_not_overlap(self):
        elements = self.steps['network']
        for key, field in elements.items():
            self.assertEqual(field['defaultValue'], self.params[key]['defaultValue'])
        vnet = ipaddress.ip_network(elements['vnetCIDR']['defaultValue'])
        subnets = [ipaddress.ip_network(v['defaultValue']) for k, v in elements.items() if k != 'vnetCIDR']
        for i, subnet in enumerate(subnets):
            self.assertTrue(subnet.subnet_of(vnet))
            for other in subnets[i+1:]:
                self.assertFalse(subnet.overlaps(other))

    def test_button_uses_matching_fork_branch_and_dedicated_form(self):
        raw = 'https://raw.githubusercontent.com/Galvy/opnazure/feature/active-backup-vpn-site/ARM/'
        for path in ('README.md', 'docs/active-backup.md'):
            text = (ROOT / path).read_text()
            urls = [url for url in re.findall(r'\]\((https://portal.azure.com/[^)]+)\)', text)
                    if '/uri/' + raw + 'active-backup.json/' in unquote(url)]
            self.assertEqual(len(urls), 1)
            url = urls[0]
            self.assertEqual(unquote(url), 'https://portal.azure.com/#create/Microsoft.Template/uri/' +
                             raw + 'active-backup.json/uiFormDefinitionUri/' + raw + 'active-backup.uiFormDefinition.json')
        self.assertEqual((ROOT / 'ARM/active-backup.uiFormDefinition.json').read_bytes(),
                         (ROOT / 'bicep/active-backup.uiFormDefinition.json').read_bytes())

if __name__ == '__main__':
    unittest.main()
