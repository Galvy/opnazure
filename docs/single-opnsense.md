# Single OPNsense Azure site

The site has one firewall with two NICs. The on-premises FortiGate remains the
company hub. Configure IPsec, OpenVPN, WireGuard and business routes manually.
There is no HA, automatic failover or upgrade switchover in this deployment.

## Deploy

Use a new resource group. The dedicated button in this branch's README opens the
guided form after the branch is published. Alternatively, upload
`ARM/single-opnsense.json` through Azure **Deploy a custom template → Build your own
template in the editor → Load file**. The file embeds the fork's bootstrap scripts;
upstream OPNsense bootstrap/packages still require Internet access.

Choose region, VM name/size (one x64 VM supporting two NICs), infrastructure CIDRs,
management public IPv4 CIDR and a temporary FreeBSD administrator password. Default
network: VNet `10.80.0.0/16`, WAN `10.80.0.0/24`, Trusted-Transit `10.80.1.0/24`,
Trusted-Servers `10.80.2.0/24`. Ensure no overlap with company or client networks.

The image remains FreeBSD 15.1 Gen2 ZFS converted to OPNsense 26.7, using the same
pinned upstream bootstrap as the existing templates. Regional SKU availability,
quota and Marketplace terms remain Azure prerequisites. No custom RBAC, Storage
witness or managed identity is created.

## Access and network

`managementURL` is `https://PUBLIC_IP` (TCP **443**, no 50443/50444 NAT mapping).
The NSG permits HTTPS from `managementSourceCIDR` and denies public SSH. Enable SSH
and adjust its rules yourself only if required. The proposed GUI port change to
50443 has not been implemented.

Wait for bootstrap and reboot, then check `/var/db/opnazure/status` and logs under
`/var/log/opnazure-bootstrap.log` and `/var/log/opnazure-firstboot.log`. Change the
initial OPNsense `root` / `opnsense` password. The temporary Azure bootstrap password
is not the converted OPNsense password.

WAN has a Standard public IP directly attached. `trustedNextHop` is the private LAN
NIC address. Create Windows VMs in Trusted-Servers without public IPs; configure
Azure UDRs via this next hop and OPNsense return routes via the trusted Azure gateway.
No UDR or workload VM is created by this template.

The NSG permits inbound UDP 500/4500 for IPsec NAT-T, plus the configured OpenVPN and
WireGuard UDP ports. This does not configure or start a VPN server: configure guest
firewall rules, VPN credentials, listeners, NAT exemptions and FortiGate return routes
manually. Restrict OpenVPN users to the intended Azure services and use separate
WireGuard addressing/rules for server connections. Retain control-plane connectivity
for Azure agent, DNS and updates when editing routing.

The VM and NIC IP-forwarding settings originate from the existing TwoNics bootstrap.
No load balancers, probes, standby gates or peer configuration are involved.

## Verification and maintenance

Before using workloads, verify management access, routing in both directions,
IPsec via both FortiGate ISP endpoints, OpenVPN access boundaries and WireGuard
connectivity. Inspect actual source addresses/NAT behavior. Local compilation and
mocked bootstrap tests do not establish Azure runtime or VPN interoperability.

For upgrades, back up configuration and plan downtime: the only firewall will reboot.
Do not rerun this fresh-install bootstrap or redeploy the template to upgrade an
existing OPNsense installation. Use the normal OPNsense upgrade procedure and
check compatibility with the Azure agent and base OS.

Build/check:

```sh
bicep build bicep/single-opnsense.bicep --outfile ARM/single-opnsense.json
python3 -m unittest discover -s tests -v
```

Rebuild ARM after changing embedded sources. The dedicated form lives in
`bicep/single-opnsense.uiFormDefinition.json` with an identical copy under `ARM`.
