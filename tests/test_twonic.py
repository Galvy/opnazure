"""Offline regressions: inputs, migration preparation and fork deployment links."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from get_nic_gw import gateway
from prepare_config import BOOTSTRAP_SHA256, CORE_COMMIT, patch_bootstrap, render_config, validate

BASE = 'https://raw.githubusercontent.com/Galvy/opnazure/update/freebsd15-opnsense26.7/'
SETTINGS = dict(scriptURI=BASE+'scripts/', opnVersion='26.7', agentMinimumVersion='2.15.0.1',
                role='TwoNics', trustedSubnet='10.0.1.0/24', windowsSubnet='')


class PreparationTests(unittest.TestCase):
    def test_gateway_large_subnet_uses_constant_memory(self):
        self.assertEqual(gateway('0.0.0.0/0'), '0.0.0.1')
        self.assertEqual(gateway('10.0.1.0/24'), '10.0.1.1')
        self.assertEqual(gateway('10.0.1.0/29'), '10.0.1.1')

    def test_invalid_subnets_rejected(self):
        for cidr in ['10.0.1.1/24', '10.0.0.0/32', '::/64', 'bad', '10.0.0.0/24;reboot']:
            with self.subTest(cidr=cidr), self.assertRaises(ValueError):
                gateway(cidr)

    def test_no_windows_does_not_create_fake_public_route(self):
        xml = render_config((ROOT/'scripts/config.xml').read_text(), SETTINGS)
        self.assertNotIn('WindowsVMSubnet', xml)
        self.assertNotIn('1.1.1.1/32', xml)
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext('gateways/gateway_item/gateway'), '10.0.1.1')
        # Do not bypass the real OPNsense migration by changing the version number.
        self.assertEqual(root.findtext('version'), '11.2')

    def test_windows_alias_and_return_route(self):
        xml = render_config((ROOT/'scripts/config.xml').read_text(), dict(SETTINGS, windowsSubnet='10.10.2.0/24'))
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext('staticroutes/route/network'), '10.10.2.0/24')
        self.assertEqual(root.findtext('staticroutes/route/gateway'), 'LAN_GW')
        self.assertIn('WindowsVMSubnet', xml)
        self.assertNotIn('zzz.zzz', xml)

    def test_invalid_settings_fail_before_conversion(self):
        for key, value in [('role','Primary'), ('opnVersion','27.1'), ('scriptURI','http://example.com/'),
                           ('scriptURI','https://example.com/;reboot/'), ('agentMinimumVersion','2;id')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(dict(SETTINGS, **{key:value}))

    def test_bootstrap_only_known_changes_and_fail_fast(self):
        source = (ROOT/'tests/fixtures/opnsense-bootstrap.sh').read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), BOOTSTRAP_SHA256)
        result = patch_bootstrap(source)
        self.assertIn('\nset -e\n', result)
        self.assertNotRegex(result, r'(?m)^\s*reboot\s*$')
        self.assertIn(f'SUBFILE="{CORE_COMMIT}"', result)
        self.assertIn("pkg unregister -fg 'FreeBSD-*'", result)
        self.assertIn('FREEBSD="15.1"', result)

    def test_modified_upstream_fails_closed(self):
        source = (ROOT/'tests/fixtures/opnsense-bootstrap.sh').read_bytes()
        with self.assertRaises(ValueError):
            patch_bootstrap(source+b'\n')


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.arm = json.loads((ROOT/'ARM/main.json').read_text())
        cls.ui = json.loads((ROOT/'bicep/uiFormDefinition.json').read_text())

    def test_deploy_button_both_urls_point_at_branch(self):
        readme = (ROOT/'README.md').read_text()
        # This test owns the existing TwoNics/Active-Active button; the manual
        # Active-Backup button has a separate template/form wiring test.
        links = [url for url in re.findall(r'https://portal.azure.com/[^)\s]+', readme)
                 if '/uri/'+BASE+'ARM/main.json' in unquote(url)]
        self.assertEqual(len(links), 1)
        link = unquote(links[0])
        self.assertIn('/uri/'+BASE+'ARM/main.json', link)
        self.assertIn('/uiFormDefinitionUri/'+BASE+'ARM/uiFormDefinition.json', link)
        self.assertNotIn('dmauser', link)

    def test_all_runtime_defaults_use_fork(self):
        self.assertEqual(self.arm['parameters']['OpnScriptURI']['defaultValue'], BASE+'scripts/')
        for folder in ['ARM','bicep']:
            params=json.loads((ROOT/folder/'main.parameters.json').read_text())['parameters']
            self.assertEqual(params['OpnScriptURI']['value'], BASE+'scripts/')
        ui_elements = [e for step in self.ui['view']['properties']['steps'] for e in step['elements']]
        uri=next(e for e in ui_elements if e['name']=='OpnScriptURI')
        self.assertEqual(uri['defaultValue'],BASE+'scripts/')
        for p in ['ARM/main.json','ARM/uiFormDefinition.json','bicep/main.bicep']:
            self.assertNotIn('dmauser', (ROOT/p).read_text())

    def test_generated_companions_match(self):
        for name in ['main.parameters.json','uiFormDefinition.json']:
            self.assertEqual((ROOT/'ARM'/name).read_bytes(), (ROOT/'bicep'/name).read_bytes())

    def test_only_supported_scenario_and_series(self):
        self.assertEqual(self.arm['parameters']['scenarioOption']['allowedValues'], ['Active-Active', 'TwoNics'])
        self.assertEqual(self.arm['parameters']['OpnVersion']['allowedValues'], ['26.7'])

    def test_image_reference_and_purchase_plan_match(self):
        nested=next(r['properties']['template'] for r in self.arm['resources'] if r['name']=="[format('{0}-TwoNics', parameters('virtualMachineName'))]")
        vm=next(r for r in nested['resources'] if r['type']=='Microsoft.Compute/virtualMachines')
        self.assertEqual(nested['variables']['imagePublisher'], 'freebsd')
        self.assertEqual(nested['variables']['imageOffer'], 'freebsd-15_1')
        self.assertEqual(nested['variables']['imageSku'], '15_1-release-amd64-gen2-zfs')
        self.assertEqual(vm['plan']['name'], vm['properties']['storageProfile']['imageReference']['sku'])
        extension=next(r for r in nested['resources'] if r['type']=='Microsoft.Compute/virtualMachines/extensions')
        self.assertIn('base64(string(', extension['properties']['settings']['commandToExecute'])
        self.assertIn('bootDiagnostics', vm['properties']['diagnosticsProfile'])

    def test_optional_arguments_round_trip(self):
        encoded=base64.b64encode(json.dumps(SETTINGS).encode())
        self.assertEqual(json.loads(base64.b64decode(encoded))['windowsSubnet'], '')


if __name__ == '__main__':
    unittest.main()
