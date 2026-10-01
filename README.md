# Single OPNsense on Azure

One OPNsense 26.7 firewall, bootstrapped from FreeBSD 15.1, with WAN/LAN NICs.
This branch focuses on a single Azure site for cloud services, IPsec to FortiGate,
OpenVPN clients and WireGuard servers. VPNs and business routing are configured manually.

## Deploy the single-firewall template

[![Deploy single OPNsense to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Fsingle-opnsense-site%2FARM%2Fsingle-opnsense.json/uiFormDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Fsingle-opnsense-site%2FARM%2Fsingle-opnsense.uiFormDefinition.json)

This dedicated button uses `ARM/single-opnsense.json` and its guided form from
`Galvy/opnazure`, branch `feature/single-opnsense-site`. It requires that branch to
be published. Before publication, the ARM JSON can be loaded directly into Azure's
custom-template editor; all fork bootstrap scripts are embedded.

The form has subscription/region selection, a VM size picker for **one** two-NIC
x64 VM, management CIDR, password confirmation, Azure subnets and UDP VPN ports.
Use a new test resource group. The initial OPNsense root password must be changed
after conversion; the Azure bootstrap password is a separate credential.

- Standard public IP directly on the WAN NIC; HTTPS management on **443** from your CIDR.
- LAN NIC as the route-table next hop; separate Trusted-Servers subnet for workloads.
- NSG permits IPsec NAT-T UDP 500/4500, OpenVPN UDP 1194 and WireGuard UDP 51820 by default.
- No load balancer, second firewall, HA agent, witness, handover script or automatic failover is deployed.
- No Windows VM, enterprise routes, VPN instances or VPN keys are installed.

See the [single-firewall deployment guide](docs/single-opnsense.md).
Local checks do not replace an Azure deployment and VPN traffic test. A single
firewall requires a maintenance interruption for reboot/upgrade.

## Previous templates

These are preserved on their existing branches and are not the current focus:

- [Manual Active-Backup: separate deployment](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Factive-backup-vpn-site%2FARM%2Factive-backup.json/uiFormDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Factive-backup-vpn-site%2FARM%2Factive-backup.uiFormDefinition.json) · [guide](docs/active-backup.md)
- [TwoNics / Active-Active: existing deployment](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Fupdate%2Ffreebsd15-opnsense26.7%2FARM%2Fmain.json/uiFormDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Fupdate%2Ffreebsd15-opnsense26.7%2FARM%2FuiFormDefinition.json) · [TwoNics guide](docs/twonics-test.md) · [Active-Active guide](docs/active-active-test.md)

Based on [dmauser/opnazure](https://github.com/dmauser/opnazure).
