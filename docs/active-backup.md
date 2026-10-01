# Manual active-backup for planned upgrades

This template supports **planned maintenance only**. Primary is initially selected
for traffic; Secondary is running and manageable but isolated from workload traffic.
There is no automatic failover after a VM, OPNsense or VPN failure.

The standalone template replaces the earlier automatic variant on this branch.
It creates no witness Storage, managed identities, custom RBAC roles, election
controller or firewall-to-firewall power permissions. Operators switch nodes using
their own Azure credentials. Configuration and VPN session synchronization are not
installed automatically.

**Validation:** Bicep compilation, shell checks and local tests. No real Azure deploy,
manual handover or VPN connectivity test has been performed for this variant.

## Deploy

Use the **Deploy Active-Backup** button at the top of this branch's README:

[![Deploy Active-Backup to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Factive-backup-vpn-site%2FARM%2Factive-backup.json/uiFormDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Factive-backup-vpn-site%2FARM%2Factive-backup.uiFormDefinition.json)

It opens the dedicated Active-Backup form: subscription/region, VM size and access,
Azure network, then VPN ports and image revision. There is no
**OPNSense Scenario** selector. The form requests a **new test resource group**.
The VM selector is scoped to the selected subscription/region and counts two VMs.
It offers x64 B2s and D2s/D4s/D8s v5 sizes with at least two NICs; actual availability
and quota remain subject to Azure checks. Password entry is masked with confirmation.
IPv4 CIDRs and port ranges are validated; Azure validates subnet containment and overlap.
Only infrastructure/publication settings are collected, not VPN keys or business routes.

Alternatively, load `ARM/active-backup.json` through Azure Portal: **Deploy a custom template → Build your own template in the editor → Load
file**. Loading the ARM JSON alone uses Azure's plain parameter form; use the button for the guided interface. Existing `ARM/main.json` buttons still deploy TwoNics/Active-Active.

The ARM file embeds the fork's scripts. It works without publishing the branch or
opening a PR. Internet access is still required for the upstream OPNsense bootstrap
and packages. `scriptURI` is source provenance, not an alternative download switch.

Parameters include:

- Region, cluster name, a VM size supporting two NICs and FreeBSD image revision.
- VNet/WAN/Trusted-Transit/Trusted-Servers CIDRs. Defaults are `10.80.0.0/16`,
  `10.80.0.0/24`, `10.80.1.0/24`, `10.80.2.0/24`. Choose non-overlapping ranges.
- Your management public IPv4 CIDR and a temporary Azure bootstrap password.
- OpenVPN UDP and WireGuard UDP ports, default 1194 and 51820; choose distinct ports.

The region must support the selected VM SKU and Marketplace image. Accept Marketplace
terms for `freebsd:freebsd-15_1:15_1-release-amd64-gen2-zfs` if required. Normal deployment
permissions are needed; this variant does not create subscription-scoped RBAC.

The template deploys two FreeBSD 15.1 → OPNsense 26.7 VMs in an availability set,
with unique WAN/LAN NIC addresses, a public Standard LB, an internal Standard LB
and separate per-node NSGs. It does not create Windows VMs, business routes, BGP
neighbors, VPN tunnels, pools, keys or user certificates.

Do not redeploy this fresh-install template over a running pair: it would reset
selection to Primary. It is **not an in-place migration** from the earlier automatic
variant. An incremental ARM deployment does not remove its old controllers, identities
or roles. Use fresh VMs/resources to test this version.

## Initial configuration

After provisioning, verify `/var/db/opnazure/status` reports `ready` on both nodes.
Logs: `/var/log/opnazure-bootstrap.log` and `/var/log/opnazure-firstboot.log`.
These are bootstrap records, not ongoing health checks. The pinned version checks
run only until the first successful boot, so later manual upgrades are not rejected
for changing the OPNsense/FreeBSD version.

Management URLs are deployment outputs:

- `https://PUBLIC_IP:50443` → Primary HTTPS 443.
- `https://PUBLIC_IP:50444` → Secondary HTTPS 443.

Both remain available from `managementSourceCIDR` while their VMs are running,
regardless of selection. OPNsense initially uses `root` / `opnsense`; change both
passwords. The Azure bootstrap password is not the converted OPNsense root password.
SSH is not published by the public LB.

Configure routing and VPNs on both nodes. Keep node-specific NIC settings distinct.
If using XMLRPC, configure selective synchronization yourself and check release
compatibility before allowing synchronization between different firmware versions.
No pfsync or automatic replication of runtime VPN sessions is configured.

Use Trusted-Servers for Windows workloads. Configure its UDRs toward the
`internalNextHop` output, plus OPNsense return routes and the FortiGate return paths.
Azure remains a peripheral site; the FortiGate headquarters remains the hub.

Public VPN rules publish UDP 500/4500 (IPsec NAT-T), OpenVPN UDP and WireGuard UDP.
Configure matching guest listeners, firewall rules and NAT exemptions. OpenVPN clients
should receive only the allowed Azure routes and be restricted by firewall rules.
Use a separate pool/policy for WireGuard servers. Validate the public source IP/port
of Azure-initiated IPsec tunnels with FortiGate; template success does not prove
outbound SNAT or return-path correctness.

## Selection mechanism

Both LBs use TCP 443 probes. This checks GUI reachability, **not VPN readiness**.
Selection is controlled by three rules in each `<cluster>-<node>-Gate` NSG:

| Rule | Selected node | Standby |
| --- | --- | --- |
| `Role-Probe` (AzureLoadBalancer → TCP 443) | Allow | Deny |
| `Workload-Inbound` | Allow | Deny |
| `Workload-Outbound` | Allow | Deny |

The standby probe is blocked independently of administrator access to its GUI.
Even if a guest service starts after an upgrade or GUI apply, its workload traffic
remains blocked outside the guest. Keep these Azure rules intact.

Standby control-plane exceptions allow restricted management, HTTPS from the transit
subnet, outbound HTTP/HTTPS for updates, NTP and Azure platform endpoints. System DNS
uses Azure directly. Do not route this control path through workload VPNs or use its
reserved ports for VPN services. This variant supports the UDP VPN endpoints above,
not OpenVPN TCP 443. Do not enable both nodes' workload/probe rules simultaneously.

## Planned upgrade and switchover

Use one operator and one console for the whole operation. No distributed lock exists;
concurrent switching, portal rule edits or template redeployment are unsupported.
Before switching, back up both configurations and verify the target's firmware,
interfaces, certificates/keys, routes and services in its GUI. Mere VM `running`
status does not establish readiness.

1. With Primary selected, update and reboot Secondary while it remains isolated.
2. Check Secondary and schedule the interruption.
3. Run the manual switch to Secondary. The helper disables the old probe, gracefully
   deallocates Primary, verifies it is stopped, closes its workload rules, then opens
   Secondary's workload rules and enables its probe last.
4. Wait for **both** LB probes to converge and test actual VPN/workload traffic.
5. Start Primary as an isolated standby, update it and verify its configuration.
6. Leave Secondary selected. Returning to Primary is optional and requires another
   planned switchover.

NSG rule changes do not terminate established connections. **Stopping the old node
before enabling the target is mandatory**, not just a probe change. Expect an outage
and VPN/client reconnection; there is no seamless session migration.

The helper requires Python 3 and Azure CLI (for example in Azure Cloud Shell),
`az login`, and operator rights to read/start/deallocate these VMs and update their
NSG rules. It is never installed or run automatically on the firewalls.

Read-only inspection:

```sh
python3 scripts/manual-switchover.py status \
  --subscription YOUR_SUBSCRIPTION_ID --resource-group YOUR_RG --cluster opn-vpn
```

Execute the planned switch after checking Secondary:

```sh
python3 scripts/manual-switchover.py switch --node Secondary \
  --subscription YOUR_SUBSCRIPTION_ID --resource-group YOUR_RG --cluster opn-vpn
```

Start the isolated Primary for maintenance:

```sh
python3 scripts/manual-switchover.py start-standby --node Primary \
  --subscription YOUR_SUBSCRIPTION_ID --resource-group YOUR_RG --cluster opn-vpn
```

A failed or unconfirmed stop prevents enabling the target. Any later failure stops
the procedure without automatic rollback; an outage may remain. Inspect `status`
and Azure operation completion before retrying. A partial switch can be explicitly
resumed with the same target. Never start a VM whose workload isolation is uncertain.

For rollback, first prepare/start Primary with its three rules at `Deny`, then invoke
`switch --node Primary`. It stops and isolates Secondary before enabling Primary.
If the selected VM is not running, inspect and repair the situation manually: the
helper's `start-standby` intentionally requires a running selected peer. There is no
unattended disaster recovery in this variant.

## Validation and maintenance

Local checks:

```sh
bicep build bicep/active-backup.bicep --outfile ARM/active-backup.json
python3 -m unittest discover -s tests -v
shellcheck scripts/embedded-bootstrap.sh scripts/configureopnsense.sh
```

Rebuild ARM whenever an embedded script changes. Tests compare the embedded payload
with source, execute the extraction launcher with a harmless bootstrap substitute,
and simulate successful/failed shutdowns, partial NSG updates, safe standby startup
and reverse handover. Existing scenario regression tests remain in place.

Before production, deploy a disposable pair and validate both management URLs,
standby isolation after GUI apply/reboot, actual IPsec/OpenVPN/WireGuard traffic,
Primary→Secondary handover, standby update and reverse handover. Record downtime
and inspect both LB backends. A sudden failure must **not** activate the standby.

References: [Azure NSG connection behavior](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview),
[Azure CLI VM operations](https://learn.microsoft.com/en-us/cli/azure/vm).
