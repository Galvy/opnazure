"""Validate inputs and prepare the existing XML for OPNsense's own migrations."""
import hashlib
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
    if settings.get("role") != "TwoNics" or settings.get("opnVersion") != "26.7":
        raise ValueError("Only TwoNics / OPNsense 26.7 is supported")
    if not re.fullmatch(r"https://[A-Za-z0-9._~:/%+-]+/", settings["scriptURI"]):
        raise ValueError("scriptURI must be an HTTPS directory URL")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}", settings["agentMinimumVersion"]):
        raise ValueError("Invalid minimum agent version")
    gateway(settings["trustedSubnet"])
    windows = settings.get("windowsSubnet", "")
    if windows:
        gateway(windows)
    return settings


def render_config(source, settings):
    settings = validate(settings)
    windows = settings.get("windowsSubnet", "")
    text = source.replace("yyy.yyy.yyy.yyy", gateway(settings["trustedSubnet"]))
    text = text.replace("zzz.zzz.zzz.zzz", windows)
    root = ET.fromstring(text)
    if root.tag != "opnsense":
        raise ValueError("Not an OPNsense configuration")
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
    (work / "config.rendered.xml").write_text(render_config((work / "config.xml").read_text(), settings))
    (work / "bootstrap.sh").write_text(patch_bootstrap((work / "bootstrap.upstream.sh").read_bytes()))
