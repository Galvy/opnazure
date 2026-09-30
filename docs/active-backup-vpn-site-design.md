# Manual active-backup: scope and invariants

The user narrowed this template to planned firmware upgrades only. The automatic
variant is superseded; the current executable template is `ARM/active-backup.json`.
See [deployment and maintenance instructions](active-backup.md).

## Scope

Azure hosts migrated services as a peripheral site. The existing FortiGate hub
remains unchanged. Business CIDRs, routing, VPNs and configuration synchronization
are managed by the operator after deployment. Azure infrastructure CIDRs are still
required at deployment time.

Primary is initially selected. Secondary is running and manageable but its workload
traffic and Azure LB probes are denied by dedicated NSG rules. Both LBs probe TCP
443; administrators can reach both GUIs from their permitted source CIDR.

No witness, managed identity, election daemon, automatic VM fencing or custom RBAC
is deployed. Nothing changes roles on a timer, guest restart or connectivity failure.

## Planned handover invariant

Only one node may be enabled for workload traffic. An operator checks the target,
then the manual helper:

1. Validates both node states and the expected NSG rule shapes.
2. Disables the old node's probe and requests graceful Azure deallocation.
3. Verifies the old VM is stopped/deallocated and isolates both traffic directions.
4. Enables target outbound/inbound workload rules and its LB probe last.

The old node must be stopped because NSG denies do not terminate existing flows.
If stopping or isolation fails, the helper does not enable the target. There is no
automatic rollback. An isolated old node can be started explicitly for its upgrade.

The operator must serialize changes. There is no protection from concurrent operator
commands, out-of-band portal edits or template redeployment. Do not redeploy over
an existing pair: defaults would reset selection. Do not use this as an in-place
migration from the older automatic-controller implementation.

## Validation boundary

Compile both templates; run shell checks, embedded-bootstrap tests and simulated
manual-handover failure tests. Test firmware/configuration compatibility separately.
The new variant is not yet validated by a real Azure deploy or VPN traffic test.
No zero-downtime guarantee is made; tunnels and clients reconnect during handover.
