#!/usr/local/bin/python3
"""One-time bootstrap firewall rules. Does not create credentials or VPN instances."""
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


def configure(path, settings):
    tree = ET.parse(path)
    root = tree.getroot()
    root.find('system/hostname').text = 'OPNsense-' + settings['node']
    # Do not import default XMLRPC passwords or overwrite peer-local settings.
    sync = root.find('hasync')
    if sync is not None:
        root.remove(sync)
    filters = root.find('filter')
    for rule in list(filters.findall('rule')):
        if (rule.findtext('descr') or '').startswith('OPNazure HA:'):
            filters.remove(rule)
    for interface in ('wan', 'lan'):
        rule = ET.Element('rule')
        for key, value in {'type': 'pass', 'interface': interface, 'ipprotocol': 'inet',
                           'protocol': 'tcp', 'statetype': 'keep state',
                           'descr': 'OPNazure HA: Azure health probe', 'direction': 'in',
                           'quick': '1'}.items():
            ET.SubElement(rule, key).text = value
        if interface == 'lan':
            ET.SubElement(rule, 'reply-to').text = 'LAN_GW'
        ET.SubElement(ET.SubElement(rule, 'source'), 'address').text = '168.63.129.16'
        dest = ET.SubElement(rule, 'destination')
        ET.SubElement(dest, 'network').text = interface + 'ip'
        ET.SubElement(dest, 'port').text = str(settings['probe_port'])
        filters.insert(0, rule)
    rule = ET.SubElement(filters, 'rule')
    for key, value in {'type': 'pass', 'interface': 'wan', 'ipprotocol': 'inet',
                       'protocol': 'udp', 'statetype': 'keep state',
                       'descr': 'OPNazure HA: OpenVPN listener'}.items():
        ET.SubElement(rule, key).text = value
    ET.SubElement(ET.SubElement(rule, 'source'), 'any').text = '1'
    dest = ET.SubElement(rule, 'destination')
    ET.SubElement(dest, 'network').text = 'wanip'
    ET.SubElement(dest, 'port').text = str(settings['openvpn_port'])
    # No rules allowing decrypted user traffic: access must be configured explicitly.
    tree.write(path, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    config = json.loads(Path('/conf/opnazure-ha/azure.json').read_text())
    path = Path('/conf/config.xml')
    if not path.exists():
        path = Path('/usr/local/etc/config.xml')
    shutil.copy2(path, '/conf/opnazure-ha/config.before-ha.xml')
    configure(path, config)
