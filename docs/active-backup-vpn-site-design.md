# Active-backup Azure site: implementation contract

Status: implemented initial variant; see [deployment guide](active-backup.md).
`ARM/active-backup.json` is executable and embeds the bootstrap artifacts. Local
checks are complete; Azure deployment and runtime failure tests remain outstanding.
Existing TwoNics and Active-Active deployments retain their behavior.

Implementation decision: Azure NSGs provide the persistent standby gate, rather
than a guest firewall rule that GUI apply might replace. They only block new
flows, so takeover still requires hard fencing. A local lease-expiry watchdog also
disables both guest NICs. The first deployment may elect without fencing only
when no owner exists and the peer gate is verified closed. Recovery of fenced
nodes is manual. Read-only Compute operation tracking is granted at subscription
scope; power-off permissions remain scoped to the peer VM. The initial template
uses the standard ARM parameter form instead of a separate portal form. VPN health
and automatic configuration replication are deliberately not inferred from the
operator's manually configured instances.

## Scope

Azure is a peripheral site hosting migrated services. The existing FortiGate
headquarters remains the hub. Operators configure business addressing, routes,
IPsec, OpenVPN and WireGuard after deployment. The template must not generate
tunnels, BGP neighbors, credentials, VPN pools or enterprise route advertisements.
Azure VNet and NIC subnet CIDRs are necessarily deployment-time parameters;
changing these later is not equivalent to changing guest VPN configuration.

Two OPNsense 26.7 guests use the existing FreeBSD 15.1 bootstrap. A dedicated
entry point and portal form must reference this fork and branch. Do not replace
the deployed scenarios or revive the deleted historical OpenVPN-only branch.

## Required safety invariant

At most one node may forward workload traffic or send/receive VPN traffic under
normal operation and the documented failure model. Management access remains
independent of the active role. Neither an HTTPS GUI probe nor pfsync establishes
exclusive ownership. Configuration replication is separate from election.

The intended design combines:

1. An exclusive finite Azure Blob lease, accessed with managed identities.
2. A boot-persistent local data-plane gate, closed by default on both nodes.
3. Azure Compute fencing before an unclean promotion: request hard power-off of
   the previous node and confirm completion and stopped/deallocated state.
4. The same lease-aware readiness decision for public and internal load balancers.

The lease arbitrates ownership; it does not stop an old VPN daemon. Load balancer
probes do not stop outgoing IPsec/WireGuard traffic or existing flows. Both the
local gate and fencing are required; service-stop polling alone is insufficient.

## State transitions

- Boot into gated standby, with management available and LB health unavailable.
- A candidate acquires the lease and keeps renewing during fencing.
- It verifies the previous node is stopped. A timeout, unknown state, denied
  request or lost lease prevents promotion.
- After successful fencing, revalidate ownership, prepare the configured local
  services, open the gate, then advertise readiness.
- On renewal failure, withdraw readiness and close the gate before the lease
  safety deadline. Use monotonic time and bound blocking operations.
- A successor must fence a frozen predecessor even when its health probe fails.
- Recovery is non-preemptive: a recovered primary does not evict a healthy backup.

Finite leases can be 15–60 seconds. A proposed initial lease is 60 seconds with
an earlier local cutoff, but this is not an end-to-end failover-time guarantee.
Never break a lease merely because the peer cannot be reached.

If Storage or Compute cannot establish exclusive ownership, fail closed. This
chooses outage over split brain. Rebooting a fenced node must not reopen its data
plane before the boot guard. Automatic recovery must also account for delayed
Compute operations from an earlier owner; lease expiry does not cancel those
operations. Do not implement unconditional peer power-on.

## Manual configuration boundary

The gate must cover forwarding and locally generated VPN traffic independently
of the number of manually configured instances. Applying configuration in the
GUI, restarting a service, rebooting or reloading firewall rules must not bypass
it. Preserve access to management, IMDS, Azure agent endpoints, DNS/time and the
Storage/Compute control plane without relying on a workload VPN or workload UDR.

VPN credentials and configuration must be available on the standby before it is
eligible. Do not treat XMLRPC synchronization as universally covering every
plugin, node-specific setting or runtime session. Specify and verify the supported
configuration synchronization procedure before enabling workload failover.

Empty VPN configurations must be supported. Local process health and remote VPN
reachability are different: loss of a FortiGate ISP must not trigger node fencing.
Service-aware checks can only be enabled for services actually configured.

## Azure resources and routing

- Two guests with unique NIC IPs and an availability placement policy.
- Public Standard LB, separate per-node management NAT rules, internal Standard
  LB with HA Ports, and a dedicated private role-aware probe.
- Private witness container, managed identities and narrowly scoped data access.
- Compute fencing permissions restricted to the peer VM; no subscription-wide
  VM Contributor grant. Identity and RBAC provisioning must precede HA enablement.
- Configurable Azure infrastructure subnet CIDRs and management source CIDR.
- No enterprise routes or workload route table associations installed implicitly.

VPN publication ports are Azure LB/NSG settings, not only OPNsense settings.
Document defaults and how an operator changes both layers. Validate outbound
IPsec NAT-T identity and return-path symmetry with actual FortiGate traffic before
claiming a stable shared VPN endpoint works. Do not publish a Deploy to Azure
button for this design until a matching executable template and artifacts exist.

## Acceptance tests before release

- Simultaneous boot and lease-acquisition race: only one node opens its gate.
- Hard VM failure, guest freeze and killed HA process: successor fences before
  forwarding; an expired health response never authorizes promotion.
- Loss of peer connectivity with both nodes healthy does not cause split brain.
- Storage outage, IMDS failure, RBAC denial, delayed lease response and failed or
  delayed Compute operations prevent unsafe promotion.
- Guest reboot, GUI apply, service restart and firewall reload cannot bypass the
  standby gate, including after a previously active VM is restarted.
- Management remains available on standby; empty VPN configuration is supported.
- Both LBs select the same active guest; old flows cannot continue on standby.
- Configure IPsec/OpenVPN/WireGuard manually, verify allowed traffic, force a
  failover and measure reconnection. Test both FortiGate ISP endpoints and the
  public source identity of tunnels initiated from Azure.
- Run regression checks for the existing TwoNics and Active-Active templates.

Local model tests and Bicep compilation cannot establish these Azure/FreeBSD
runtime guarantees. Record actual deployment and fault-injection results before
describing the scenario as operational.

## References

- [Azure Blob lease](https://learn.microsoft.com/en-us/rest/api/storageservices/lease-blob)
- [Azure VM power-off](https://learn.microsoft.com/en-us/rest/api/compute/virtualmachines/virtualmachines-stop)
- [Azure NVA high availability](https://learn.microsoft.com/en-us/azure/architecture/networking/guide/network-virtual-appliance-high-availability)
