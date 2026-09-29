"""Validate inputs and prepare the existing XML for OPNsense's own migrations."""
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from get_nic_gw import gateway

BOOTSTRAP_COMMIT = "db018c35aac47020c69dc507c3ae67a30dbdf2ab"
BOOTSTRAP_SHA256 = "ed7c0d9739f375cc941fab7d23033717e1dd16aa2758fc8e544cdb4b90b4f57c"
CORE_COMMIT = "791286d4dec8ffeba3841901ff91129c63db0af8"


def validate(settings):
    if settings.get("role") not in ("TwoNics", "Primary", "Secondary") or settings.get("opnVersion") != "26.7":
        raise ValueError("Supported roles: TwoNics, Primary, Secondary; OPNsense series: 26.7")
    if not re.fullmatch(r"https://[A-Za-z0-9._~:/%+-]+/", settings["scriptURI"]):
        raise ValueError("scriptURI must be an HTTPS directory URL")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}", settings["agentMinimumVersion"]):
        raise ValueError("Invalid minimum agent version")
    gateway(settings["trustedSubnet"])
    windows = settings.get("windowsSubnet", "")
    if windows:
        gateway(windows)
    if settings["role"] != "TwoNics":
        public = ipaddress.IPv4Address(settings.get("publicIPAddress", ""))
        if public.is_unspecified or public.is_multicast:
            raise ValueError("Invalid external load-balancer IPv4 address")
        subnet = ipaddress.IPv4Network(settings["trustedSubnet"])
        local = ipaddress.IPv4Address(settings.get("localTrustedIP", ""))
        peer = ipaddress.IPv4Address(settings.get("peerTrustedIP", ""))
        for address in (local, peer):
            if address not in subnet or not (subnet.network_address + 3 < address < subnet.broadcast_address):
                raise ValueError("pfsync addresses must be assignable hosts in the trusted Azure subnet")
        if local == peer:
            raise ValueError("pfsync peer must be a different node")
    return settings


def config_filename(settings):
    role = validate(settings)["role"]
    return {"TwoNics": "config.xml", "Primary": "config-active-active-primary.xml",
            "Secondary": "config-active-active-secondary.xml"}[role]


def render_config(source, settings):
    settings = validate(settings)
    windows = settings.get("windowsSubnet", "")
    text = source.replace("yyy.yyy.yyy.yyy", gateway(settings["trustedSubnet"]))
    text = text.replace("zzz.zzz.zzz.zzz", windows)
    text = text.replace("www.www.www.www", settings.get("publicIPAddress", ""))
    text = text.replace("xxx.xxx.xxx.xxx", settings.get("peerTrustedIP", ""))
    root = ET.fromstring(text)
    if root.tag != "opnsense":
        raise ValueError("Not an OPNsense configuration")
    role = settings["role"]
    if role != "TwoNics":
        root.find("system/hostname").text = f"OPNsense-{role}"
        ha = root.find("hasync")
        if ha is None:
            ha = ET.SubElement(root, "hasync")
        # Both sides must send pfsync to a unicast peer; Azure does not carry multicast.
        for tag, value in (("pfsyncenabled", "on"), ("pfsyncinterface", "lan"),
                           ("pfsyncpeerip", settings["peerTrustedIP"])):
            node = ha.find(tag)
            if node is None:
                node = ET.SubElement(ha, tag)
            node.text = value
        if role == "Primary":
            ha.find("synchronizetoip").text = settings["peerTrustedIP"]
        else:
            # The secondary sends states, but never pushes configuration back.
            for node in list(ha):
                if node.tag.startswith("synchronize") or node.tag in ("username", "password"):
                    ha.remove(node)
    if not windows:
        # Remove the optional subnet alias and every rule referencing it.
        for parent in root.iter():
            for child in list(parent):
                if child.tag in ("rule", "alias") and any(
                    node.text == "WindowsVMSubnet" for node in child.iter()
                ):
                    parent.remove(child)
    else:
        # Return traffic for a separate management subnet goes via Azure's LAN gateway.
        routes = root.find("staticroutes")
        if routes is None:
            routes = ET.SubElement(root, "staticroutes")
        route = ET.SubElement(routes, "route")
        for tag, value in (("network", windows), ("gateway", "LAN_GW"),
                           ("descr", "Azure management subnet")):
            ET.SubElement(route, tag).text = value
    for name, device in (("wan", "hn0"), ("lan", "hn1")):
        if root.findtext(f"interfaces/{name}/if") != device:
            raise ValueError(f"Unexpected {name} interface")
    # Preserve the old config version: OPNsense must run the real migrations.
    rendered = ET.tostring(root, encoding="unicode")
    if re.search(r"(?:xxx|yyy|zzz|www)\.(?:xxx|yyy|zzz|www)\.", rendered):
        raise ValueError("Unresolved configuration placeholder")
    return '<?xml version="1.0"?>\n' + rendered + "\n"


def patch_bootstrap(source):
    if hashlib.sha256(source).hexdigest() != BOOTSTRAP_SHA256:
        raise ValueError("Bootstrap checksum mismatch; refusing to modify or execute it")
    text = source.decode()
    replacements = {
        '\treboot\n': '\t: # Caller reboots only after Azure integration is installed.\n',
        'SUBFILE="stable/${RELEASE}"': f'SUBFILE="{CORE_COMMIT}"',
        'SUBDIR="stable-${RELEASE}"': f'SUBDIR="{CORE_COMMIT}"',
        'FREEBSD="%%FREEBSD%%"': 'FREEBSD="15.1"',
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise ValueError(f"Unexpected bootstrap layout: {old!r}")
        text = text.replace(old, new)
    if "\nset -e\n" not in text:
        raise ValueError("Bootstrap must retain fail-fast behaviour")
    return text


if __name__ == "__main__":
    work = Path(sys.argv[1])
    settings = validate(json.loads((work / "settings.json").read_text()))
    (work / "config.rendered.xml").write_text(render_config((work / config_filename(settings)).read_text(), settings))
    (work / "bootstrap.sh").write_text(patch_bootstrap((work / "bootstrap.upstream.sh").read_bytes()))
