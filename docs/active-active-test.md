# Active-Active Azure test

## Architecture

Select `Active-Active` in the same **Deploy to Azure** wizard linked from this branch's README.
The template, UI, scripts and all three XML configuration templates are loaded from the fork.
FreeBSD 15.1 / OPNsense 26.7, error handling, pinned bootstrap and Azure Agent installation
are shared with TwoNics.

This scenario creates:

- Two VMs, `<name>-Primary` and `<name>-Secondary`, in an aligned availability set
  with two fault domains and two update domains. It does not span availability zones.
- Four distinct NICs, with IP forwarding enabled: each VM has WAN (`hn0`) and LAN (`hn1`).
- An external Standard Load Balancer with the shared public IP, an outbound rule,
  the original TCP 3389 floating-IP example rule and inbound management NAT rules.
- An internal Standard Load Balancer on the trusted subnet with an HA-ports (`All`, port 0) rule.
- TCP 443 health probes on both LBs, allowed by the NSG and the inherited firewall rules.
- An external public /32 IP alias on both OPNsense WAN interfaces for floating-IP handling.
- Unicast pfsync in both directions over the trusted subnet, with each peer set to the other VM's
  actual trusted NIC IP. Configuration synchronization remains one-way, Primary → Secondary.

Both NIC pairs are provisioned before either VM, so reciprocal peer addresses do not introduce
circular VM deployment dependencies. The two VM extensions can run independently.

This is the upstream **active-active Azure load-balancer architecture**, not CARP or active-backup.
A TCP 443 probe tests whether the GUI port accepts connections; it does not test routing,
OpenVPN health, configuration synchronization or state synchronization. The local guest status
`ready` has the same limited scope described in the TwoNics guide.

## First deployment

1. Use a fresh test Resource Group, preferably a new VNet, `Active-Active`, and Windows disabled.
2. Supply your public IPv4 management CIDR. The backend NSG matches translated port **443**;
   you do not need to open backend ports 50443/50444. TCP 3389 for the inherited example is
   also restricted to that CIDR; configure any additional service explicitly.
3. Wait for **both** VM extensions and **both** reboots. Check Azure VM Agent status is Ready on both.
4. Open the management URLs shown in deployment outputs:
   - Primary: `https://<public-IP>:50443`
   - Secondary: `https://<public-IP>:50444`
5. Change `root / opnsense` on both firewalls and update the Primary's HA synchronization credentials.
   The initial credentials are inherited from the original templates. Do not configure reverse XMLRPC sync.
6. Check that both guests run the same OPNsense 26.7 maintenance version and FreeBSD 15.1 kernel/userspace.
   Run `/usr/local/sbin/opnazure-verify` and inspect `/var/db/opnazure/status` on each node.
7. Check each node's HA settings: `lan` for pfsync, the other node's trusted IP as peer, and
   the secondary's trusted IP as the Primary's configuration-sync destination. Verify `ifconfig pfsync0`
   and actual state transfer while generating test traffic. Same-subnet NSG/guest rules must permit pfsync.
8. Make a harmless test alias/rule change on Primary, synchronize, and verify it on Secondary.
   Also confirm each node retains its own hostname, NIC addresses and reciprocal peer settings.

The management NAT rules select a particular node and are independent of the balancing probe.
Port 50443 will not switch to Secondary if Primary fails; port 50444 will not switch to Primary.

## Forwarding and failure tests

Use a **separate client subnet**. Associate its default route with the output `trustedIPAddress`,
which is the **internal LB frontend** for this scenario. Do not use one node's LAN address as
that next hop. The `primaryTrustedIPAddress` and `secondaryTrustedIPAddress` outputs identify
individual nodes for diagnostics; they are not the shared routing endpoint.

Configure the client subnet's permissions, outbound NAT and return route via the Azure LAN gateway
on Primary, then verify the intended configuration is synchronized to Secondary. As with the
original template, an optional Windows route table must be associated with its subnet manually.

Verify:

1. Both LB backend pools contain the correct two NICs and both backends are healthy on both LBs.
2. DNS/HTTPS and the required private destinations work from the client subnet. Use multiple
   independent connections and captures/counters on each firewall; one flow alone cannot prove balancing.
3. Test an abrupt stop of Primary during an approved test window. Wait for probe convergence,
   verify new client connections through Secondary, and record what happens to existing connections.
4. Restore Primary, verify agent/probe recovery, then repeat with Secondary stopped.
5. Reboot both nodes separately and recheck configuration sync, state sync, probes and client forwarding.

Azure probe convergence is not instantaneous. Existing flows can reset; session continuity must be
measured and is not guaranteed by this template. OpenVPN process/TLS sessions are not replicated by
pfsync, and this branch does not configure an OpenVPN service. Do not infer OpenVPN HA from passing
routing or GUI checks.

## What has and has not been verified

Offline checks cover ARM generation, selectable scenarios, LB rules/probes/outbound wiring,
conditional standalone NIC creation, reciprocal pfsync inputs, Primary-only configuration sync,
per-role XML rendering and mocked bootstrap execution for both roles.

No live Azure deployment or failure injection has been performed. Regional image/VM availability,
availability-set support, bootstrap completion, guest migration, actual pfsync/XMLRPC operation,
LB health, routing symmetry and failover behaviour must be validated in this test.

For logs, image-version pinning and recovery after a failed conversion, see the
[shared diagnostics in the TwoNics guide](twonics-test.md#diagnostics).

References:
- [Azure LB health probes](https://learn.microsoft.com/en-us/azure/load-balancer/load-balancer-custom-probe-overview)
- [Azure floating-IP configuration](https://learn.microsoft.com/en-us/azure/load-balancer/load-balancer-floating-ip)
- [OPNsense synchronization settings](https://docs.opnsense.org/manual/how-tos/carp.html)
