"""Regression coverage for reciprocal peers, distinct NICs and Azure LB wiring."""
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_config import config_filename, render_config, validate


def settings(role, windows=''):
    return dict(scriptURI='https://example.test/scripts/',opnVersion='26.7',
                agentMinimumVersion='2.15.0.1',role=role,trustedSubnet='10.0.1.0/24',
                windowsSubnet=windows,publicIPAddress='203.0.113.10',
                localTrustedIP='10.0.1.4' if role=='Primary' else '10.0.1.5',
                peerTrustedIP='10.0.1.5' if role=='Primary' else '10.0.1.4')


class ActiveConfigTests(unittest.TestCase):
    def test_both_nodes_have_reciprocal_unicast_pfsync_and_shared_public_alias(self):
        for role in ('Primary','Secondary'):
            with self.subTest(role=role):
                params=settings(role)
                xml=render_config((ROOT/'scripts'/config_filename(params)).read_text(),params)
                tree=ET.fromstring(xml)
                self.assertEqual(tree.findtext('system/hostname'),'OPNsense-'+role)
                self.assertEqual(tree.findtext('hasync/pfsyncpeerip'),params['peerTrustedIP'])
                self.assertEqual(tree.findtext('hasync/pfsyncinterface'),'lan')
                self.assertEqual(tree.findtext('hasync/pfsyncenabled'),'on')
                self.assertEqual(tree.findtext('virtualip/vip/subnet'),params['publicIPAddress'])
                self.assertEqual(tree.findtext('virtualip/vip/mode'),'ipalias')
                self.assertNotIn('WindowsVMSubnet',xml)
                self.assertNotRegex(xml,r'(xxx|yyy|zzz|www)\.\1')
                self.assertNotIn('<mode>carp</mode>',xml)
                if role=='Primary':
                    self.assertEqual(tree.findtext('hasync/synchronizetoip'),params['peerTrustedIP'])
                else:
                    self.assertIsNone(tree.find('hasync/synchronizetoip'))
                    self.assertIsNone(tree.find('hasync/synchronizerules'))

    def test_windows_config_on_both_nodes(self):
        for role in ('Primary','Secondary'):
            params=settings(role,'10.0.2.0/24')
            root=ET.fromstring(render_config((ROOT/'scripts'/config_filename(params)).read_text(),params))
            self.assertEqual(root.findtext('staticroutes/route/gateway'),'LAN_GW')
            self.assertEqual(root.findtext('staticroutes/route/network'),'10.0.2.0/24')

    def test_missing_invalid_or_self_peer_rejected(self):
        for changes in [dict(peerTrustedIP=''),dict(publicIPAddress='bad'),dict(peerTrustedIP='10.0.1.4'),
                        dict(peerTrustedIP='10.0.2.5'),dict(peerTrustedIP='10.0.1.1'),
                        dict(localTrustedIP='10.0.1.255'),dict(peerTrustedIP='::1')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(dict(settings('Primary'),**changes))


class ActiveTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=json.loads((ROOT/'ARM/main.json').read_text())
        cls.module=next(r for r in cls.root['resources'] if 'ActiveActive' in r['name'])
        cls.template=cls.module['properties']['template']
        cls.resources=cls.template['resources']

    def deployment(self,name):
        return next(r for r in self.resources if r.get('name')==name)

    def test_scenario_is_conditional_and_portal_preserves_twonics_default(self):
        self.assertEqual(self.module['condition'],"[equals(parameters('scenarioOption'), 'Active-Active')]")
        single=next(r for r in self.root['resources'] if '-TwoNics' in r['name'])
        self.assertEqual(single['condition'],"[equals(parameters('scenarioOption'), 'TwoNics')]")
        ui=json.loads((ROOT/'ARM/uiFormDefinition.json').read_text())
        selector=next(e for step in ui['view']['properties']['steps'] for e in step['elements'] if e['name']=='scenarioOption')
        self.assertEqual(selector['defaultValue'],'TwoNics')
        self.assertEqual({v['value'] for v in selector['constraints']['allowedValues']},{'TwoNics','Active-Active'})

    def test_management_nat_probes_and_explicit_outbound(self):
        external=self.deployment("[variables('externalLoadBalanceName')]")['properties']['parameters']
        nats=external['inboundNatRules']['value']
        self.assertEqual([r['properties']['frontendPort'] for r in nats],[50443,50444])
        self.assertTrue(all(r['properties']['backendPort']==443 for r in nats))
        self.assertEqual(external['probe']['value'][0]['properties']['port'],443)
        self.assertEqual(external['outboundRules']['value'][0]['properties']['protocol'],'All')
        rule=external['loadBalancingRules']['value'][0]['properties']
        self.assertTrue(rule['enableFloatingIP'])
        self.assertTrue(rule['disableOutboundSnat'])
        self.assertNotIn('backendAddressPools',rule)  # Do not supply both pool forms.
        internal=self.deployment("[variables('internalLoadBalanceName')]")['properties']['parameters']
        rule=internal['loadBalancingRules']['value'][0]['properties']
        self.assertEqual((rule['protocol'],rule['frontendPort'],rule['backendPort']),('All',0,0))

    def test_distinct_nics_no_public_ip_and_no_circular_vm_dependency(self):
        self.assertEqual(self.template['variables']['roles'],['Primary','Secondary'])
        loops={r['copy']['name']:r for r in self.resources if 'copy' in r}
        self.assertEqual(set(loops),{'wanNics','lanNics','nodes'})
        for name in ('wanNics','lanNics'):
            nic=loops[name]
            self.assertNotIn('publicIPId',nic['properties']['parameters'])
            self.assertTrue(nic['properties']['parameters']['enableIPForwarding']['value'])
            self.assertFalse(any('nodes' in dep for dep in nic['dependsOn']))
        params=loops['nodes']['properties']['parameters']
        peer=params['ShellScriptObj']['value']['peerTrustedIP']
        self.assertIn('sub(1, copyIndex())',peer)
        self.assertIn('availabilitySetId',params)
        self.assertEqual(set(params['providedNics']['value']),{'wanId','lanId','wanIP','lanIP'})
        # A supplied NIC pair suppresses the VM module's standalone NIC resources.
        vmtemplate=loops['nodes']['properties']['template']
        nicmods=[r for r in vmtemplate['resources'] if r['type']=='Microsoft.Resources/deployments']
        self.assertEqual(len(nicmods),2)
        self.assertTrue(all(r['condition']=="[empty(parameters('providedNics'))]" for r in nicmods))

    def test_internal_lb_is_route_next_hop_and_both_management_urls_exposed(self):
        self.assertIn('internalLoadBalancerIP',self.root['outputs']['trustedIPAddress']['value'])
        self.assertIn(':50443',self.root['outputs']['managementURL']['value'])
        self.assertIn(':50444',self.root['outputs']['secondaryManagementURL']['value'])
        nsg=next(r for r in self.root['resources'] if r['name']=="[variables('networkSecurityGroupName')]")
        rules=nsg['properties']['parameters']['securityRules']['value']
        probe=next(r for r in rules if r['name']=='Azure-Health-Probe')['properties']
        self.assertEqual(probe['sourceAddressPrefix'],'AzureLoadBalancer')
        self.assertEqual(probe['destinationPortRange'],'443')


if __name__=='__main__': unittest.main()
