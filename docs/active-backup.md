# Active-backup VPN site on Azure

This is a separate, executable fresh-deployment template for two OPNsense 26.7
VMs bootstrapped from FreeBSD 15.1. Azure remains a peripheral site; the existing
FortiGate hub and enterprise routing are not changed.

**Status:** ARM/Bicep compilation and local fault-injection tests only. No Azure
active-backup deployment or real VPN failover has been validated by this change.
Use a new test resource group. Successful ARM provisioning is not proof of VPN
connectivity or HA readiness; follow the acceptance steps below.

## Deploy from the local artifact

Use **`ARM/active-backup.json`**, not the existing `ARM/main.json` or its scenario
selector. The ARM file embeds the fork's bootstrap, configuration renderer and HA
controller, so no branch publication, GitHub token or PR is needed. Upstream
OPNsense bootstrap and package repositories still require Internet access.

In Azure Portal:

1. Open **Deploy a custom template**.
2. Choose **Build your own template in the editor**, then **Load file**.
3. Load `ARM/active-backup.json` and save.
4. Select a new resource group and region. Enter the management public IPv4 CIDR
   and a temporary bootstrap administrator password. Adjust the infrastructure
   CIDRs before deployment if the defaults overlap your networks.
5. Review the resources and submit when ready.

The defaults are a `10.80.0.0/16` VNet, `10.80.0.0/24` WAN,
`10.80.1.0/24` Trusted-Transit and `10.80.2.0/24` Trusted-Servers. No Windows VMs,
enterprise UDRs, VPN instances, VPN credentials or BGP neighbors are created.
The two VM NICs keep distinct private IPs. Do not put Windows workloads in the
transit subnet; create them in Trusted-Servers and configure routing manually.

Prerequisites:

- Region availability/quota for the selected two-NIC VM size, FreeBSD Marketplace
  image and Standard_ZRS Storage. The template uses an availability set, not zones.
- Marketplace terms accepted for `freebsd:freebsd-15_1:15_1-release-amd64-gen2-zfs`.
- Rights to deploy resources, custom roles and role assignments. Compute operation
  status requires a narrowly scoped **read-only action at subscription scope**;
  this template includes a subscription deployment for that assignment. RG-only
  Contributor access is insufficient.
- Public Azure cloud endpoints. Sovereign-cloud endpoint variations are not
  supported by this initial controller.

`scriptURI` records source provenance and defaults to this fork/branch. All files
used from that URI are embedded, including the HA installer. It is not a switch
for downloading alternative scripts. Change sources and rebuild ARM instead.

Do not redeploy this fresh-install template over a running HA cluster: the NSG
resource definitions start closed and would reset the active gates. Do not delete
or recreate the witness to force failover. No public Deploy to Azure link is
provided until the branch is published; local file deployment works independently.

## Resources and access

- Two VMs, each with its own managed identity and NSG on both NICs.
- Public Standard LB with UDP 500/4500, OpenVPN UDP 1194 and WireGuard UDP 51820.
  The latter two ports are parameters and must be distinct. Configure matching
  OPNsense listeners/firewall rules yourself. Use IPsec NAT-T; this is not an ESP
  protocol load balancer.
- Management NAT: public TCP 50443 -> Primary TCP 443; 50444 -> Secondary TCP 443.
  These identify nodes, not the active role, and are restricted by
  `managementSourceCIDR` even when workload gates are open. SSH is not published.
- Internal Standard LB with HA Ports. `internalNextHop` is the output to use when
  configuring Windows subnet route tables. Return routes in OPNsense are also
  required. The template does not install them.
- Both LBs use the same HTTP role probe on private TCP 8080 from Azure's platform
  address. OPNsense's GUI is not the role probe.
- A private Storage container for a 60-second exclusive lease. Access uses managed
  identity, not account keys. A node may only power off its peer VM. Both identities
  may update rules on the two dedicated NSGs. No VM start permission is granted.

Control-plane exceptions remain open on standby: restricted management, HTTPS
between the transit NICs, Azure platform endpoints, outbound HTTP/HTTPS and NTP.
Guest system DNS uses Azure's resolver directly, avoiding dependency on recursive
DNS or a workload VPN. Keep that control path available when configuring routing.
**Do not use control-plane ports for VPN services** or remove/override the generated
NSG rules. This initial variant supports the UDP VPN endpoints listed above, not
OpenVPN TCP 443. The gate is not a defense against an administrator altering Azure
RBAC/NSGs or executing arbitrary root commands.

## Election and sudden failure

Both NSGs initially deny workload traffic. After first-boot checks, a controller
closes its own gate, obtains the Blob lease and records ownership. Only the first
bootstrap of a new witness can skip fencing, and only after verifying the peer's
NSG workload rules are closed. This lets both new nodes remain running for setup.

Every subsequent promotion performs these steps under a renewed lease:

1. If the peer is not already stopped/deallocated, request hard power-off
   (`skipShutdown=true`).
2. Wait for the Compute operation to succeed and the VM to report stopped or
   deallocated. A probe failure alone is never sufficient.
3. Close both peer NSG workload rules and verify the updates completed.
4. Open the candidate's workload rules, revalidate the lease, then return HTTP 200
   to both load balancers.

No preemption or automatic failback. A failed/fenced node stays stopped; there is
no automatic power-on that could race with an outstanding fencing operation.
A lease renewal outage gets a local cutoff at 40 seconds from the start of the
last successful renewal. A watchdog withdraws readiness and disables both guest
NICs at that cutoff. Management of that failed node is also lost until recovery.
A frozen/killed controller may not perform this local action; the successor still
MUST fence it. Ambiguous promotion failures locally isolate the candidate rather
than retrying a potentially unsafe gate opening.

If Storage/Compute/identity services cannot establish safe ownership, service may
remain unavailable. This design prioritizes avoiding split brain. NSG updates do
not terminate established flows; they are not a replacement for hard fencing.

Failover time includes lease expiration (up to 60 seconds), Azure API/RBAC/network
latency, fencing, NSG propagation, LB probes and VPN reconnection. No fixed RTO or
uninterrupted VPN sessions are promised. Allow minutes in the initial test plan.

The probe checks **role/lease ownership after first-boot validation**, not the health
of every manually configured VPN. A remote ISP outage must be handled by tunnel
routing, not by switching firewall nodes. This version does not detect an individual
OpenVPN/IPsec/WireGuard daemon hang if the controller and VM remain healthy.

## Configure and inspect

1. Wait for successful provisioning and `/var/db/opnazure/status` = `ready` on both
   guests. Inspect `/var/log/opnazure-bootstrap.log` and
   `/var/log/opnazure-firstboot.log` if either guest does not finish.
2. Open both management URLs. The converted OPNsense configuration initially uses
   `root` / `opnsense`, as in the existing bootstrap. **Change it on both nodes**.
   The Azure bootstrap administrator password is not the OPNsense root password.
3. Inspect `/var/log/opnazure-ha.log`. Exactly one running node should report
   `ACTIVE`. On the guest console this read-only command checks its LB readiness:

   ```sh
   fetch -q -o /dev/null http://127.0.0.1:8080/health && echo ACTIVE || echo NOT-ACTIVE
   ```

4. Configure VPNs, certificates/keys, NAT exemptions, routes and firewall rules on
   both guests. Configure the same service identity where required, but preserve
   each node's unique NIC addressing. The standby can run its UDP daemons, but its
   NSG blocks their workload traffic. BGP traffic is blocked there too.
5. There is **no automatic XMLRPC or pfsync configuration** in this template.
   Manually maintain both nodes, or configure and test selective XMLRPC replication
   from a designated configuration source. Do not synchronize node-specific Azure
   identity/HA files. Runtime VPN session state is not replicated by this controller.
6. For workloads, add Azure UDRs toward `internalNextHop` and return routes in
   OPNsense via the trusted Azure gateway. Configure headquarters/site return routes.
   Keep Azure control-plane traffic on the WAN path, independent of these VPNs.
7. OpenVPN client access must be restricted to Azure services in firewall rules.
   WireGuard requires its own pool/rules. No tunnel or pool is pre-created.

Do not stop/restart the controller as a routine GUI operation: stopping an active
controller intentionally isolates its interfaces. The standby must already contain
usable VPN/routing configuration before you rely on workload failover.

## Acceptance test in a disposable deployment

- Verify only one node returns HTTP 200 and both management URLs work.
- Confirm actual IPsec flows through both ISP endpoints, including tunnels initiated
  by OPNsense. Check the public source IP/port and return-path symmetry: the public
  LB's outbound SNAT must be verified with FortiGate, not inferred from ARM success.
- Verify OpenVPN Windows clients reach only the intended Azure services and test
  WireGuard server traffic. Confirm standby traffic is blocked in both directions.
- Hard-stop the active VM in Azure. Verify peer power-off completion/quarantine,
  one active LB backend and client/tunnel recovery. Record timestamps and packets.
- Separately test a killed controller and loss of Storage/Compute access. Never
  simulate Storage loss by deleting or breaking the witness lease.
- Reboot/apply configuration on a healthy standby; it must remain gated.
- Confirm the template still starts without any VPN configured. Empty VPN config
  is expected at initial deployment and does not prevent election.

These Azure/FreeBSD tests have **not** been run by the local validation suite.

## Rejoin a fenced node

First repair the cause of failure and confirm the surviving node is active. In
Azure, verify all earlier power-off operations have completed and the stopped
node's `Workload-Inbound` and `Workload-Outbound` rules both read `Deny`, with NSG
provisioning complete. If an operation failed or its outcome is unknown, resolve
that before starting the node. The survivor closes these gates during takeover.

Start the stopped VM manually. It must finish boot, close/verify its own gate and
remain standby because the surviving owner still holds the lease. Verify both
management and its HTTP 503 response before considering redundancy restored.
Do not force-open its gates. Keep it stopped if quarantine cannot be verified.

## Build, validation and cleanup

```sh
bicep build bicep/active-backup.bicep --outfile ARM/active-backup.json
python3 -m unittest discover -s tests -v
shellcheck scripts/embedded-bootstrap.sh scripts/configureopnsense.sh
```

Rebuild ARM whenever an embedded script/configuration changes. The test suite
compares embedded artifacts byte-for-byte and executes the real extraction launcher
with a harmless substitute for the privileged conversion command. Controller tests
inject lease conflicts, expiration, failed fencing and partial gate updates.

Deleting the test resource group removes the VMs, LB, Storage and identities.
The subscription-scoped operation-status role/assignments and subscription deployment
record must also be removed explicitly after verifying they belong to this test
cluster. Record their identifiers from the `opn-ha-status-*` deployment. Do not
remove another cluster's roles.

References: [Azure Blob leases](https://learn.microsoft.com/en-us/rest/api/storageservices/lease-blob),
[NSG connection behavior](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview),
[asynchronous operations](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/async-operations),
[Compute operation permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute).
