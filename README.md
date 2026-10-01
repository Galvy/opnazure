# OPNsense on Azure — manual Active-Backup

## Deploy Active-Backup for planned upgrades

[![Deploy Active-Backup to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Factive-backup-vpn-site%2FARM%2Factive-backup.json/uiFormDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Ffeature%2Factive-backup-vpn-site%2FARM%2Factive-backup.uiFormDefinition.json)

**Use this button for the new manual Active-Backup template.** It loads
`ARM/active-backup.json` from `Galvy/opnazure`, branch
`feature/active-backup-vpn-site`, with its dedicated guided form and VM size selector.
There is no **OPNSense Scenario** selector: this template deploys only Active-Backup.
If you see that selector, you have opened the older deployment form.

Primary starts selected; Secondary stays isolated and manageable. There is no
automatic failover. Configure VPNs and business routing manually after deployment.
Use a **new test resource group**. Enter your management public IPv4 CIDR and a
bootstrap administrator password; adjust the infrastructure CIDRs as needed.

See the [deployment and maintenance guide](docs/active-backup.md).
Scripts are embedded. Local validation is complete; real Azure handover remains
to be tested. Opening the button does not deploy resources until you submit the form.

## Existing TwoNics and Active-Active templates

The separate button below retains the existing two-scenario form and its original
update branch. It does **not** deploy the manual Active-Backup template.

This branch of [Galvy/opnazure](https://github.com/Galvy/opnazure/tree/update/freebsd15-opnsense26.7)
prepares a fresh **FreeBSD 15.1 AMD64 Gen2 ZFS VM → latest OPNsense 26.7 maintenance release**.
It is based on [dmauser/opnazure](https://github.com/dmauser/opnazure).

**Status: an initial TwoNics Azure test failed in CustomScriptForLinux preprocessing before the bootstrap ran.**
The inline-launcher correction is covered by offline regression tests; a new Azure test is still required.
Local checks cover template compilation, artifact consistency, configuration preparation, active-active LB/peer wiring and
bootstrap failure/reboot behaviour with mocked FreeBSD commands. They do not prove Azure runtime compatibility.
Choose **TwoNics** (one VM) or **Active-Active** (two VMs with Azure Standard load balancers).
The active-active architecture follows the original project, with reciprocal unicast pfsync peers.
It is not active-backup, and it does not configure an OpenVPN server or replicate OpenVPN sessions.

### Deploy TwoNics or Active-Active (separate template)

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Fupdate%2Ffreebsd15-opnsense26.7%2FARM%2Fmain.json/uiFormDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FGalvy%2Fopnazure%2Fupdate%2Ffreebsd15-opnsense26.7%2FARM%2FuiFormDefinition.json)

The button loads **both** `ARM/main.json` and `ARM/uiFormDefinition.json` from
`Galvy/opnazure`, branch `update/freebsd15-opnsense26.7`.
The form, Bicep and sample parameters all use this script directory:

```text
https://raw.githubusercontent.com/Galvy/opnazure/update/freebsd15-opnsense26.7/scripts/
```

1. Open this branch's README, then click the button above.
2. Use a **new test Resource Group**, preferably with a new VNet, and leave **Deploy Windows** unchecked for the first test.
3. Enter **your public IPv4 CIDR** (for example your address followed by `/32`) in the management field.
   The NSG limits inbound SSH/HTTPS to that source. The sample `203.0.113.10/32` is a documentation address; replace it.
4. Choose **TwoNics** or **Active-Active**, then keep OPNsense series **26.7**, image revision **latest**, bootstrap filename and fork script URL at their defaults.
5. Select an available x64 VM size supporting two NICs. The default is `Standard_B2s` (4 GiB); a larger size can help the first test.
6. Review Marketplace terms and the cost estimate, then deploy when ready.
7. Wait for the extension **and the subsequent reboot** (both nodes for active-active).
   TwoNics: `https://<public-IP>`. Active-active: `https://<public-IP>:50443` (Primary) and `:50444` (Secondary).
   Initial credentials are inherited from upstream: **root / opnsense**. Change the password on first login.
8. Follow the [TwoNics test guide](docs/twonics-test.md) or [Active-Active test guide](docs/active-active-test.md) before considering the deployment successful.
   For active-active, change the password on both nodes and update the Primary's HA synchronization credentials.

Opening the button does not deploy anything until you submit the Azure wizard. No GitHub `AZURE_CREDENTIALS`
secret is needed for a portal deployment; Azure uses your signed-in account.

## Deployment scenarios

| Scenario | VMs | Public access | Route-table next hop |
|---|---|---|---|
| TwoNics | One, WAN + LAN | Public IP on WAN; HTTPS 443 | VM's trusted NIC IP |
| Active-Active | Two, each WAN + LAN; shared availability set | Public Standard LB; management NAT 50443/50444 → 443 | Internal Standard LB frontend IP |
| Active-Backup (dedicated button at top) | Two, manual selection for planned upgrades | UDP VPN publication; management NAT 50443/50444 → 443 | Internal Standard LB frontend IP |

Active-active restores the original public TCP 3389 floating-IP example rule, the explicit
outbound SNAT rule, internal HA-ports rule and TCP 443 probes. The example inbound service
still needs OPNsense NAT/firewall configuration; it is not an OpenVPN listener. NSG access
for TCP 3389 is limited to the supplied management CIDR. Other published services need
matching LB, NSG and OPNsense rules.

Both firewalls have distinct NIC IPs. The external LB public /32 is an OPNsense WAN IP alias
on both nodes for floating-IP rules. Configuration sync goes Primary → Secondary;
pfsync uses the opposite trusted NIC address on **both** nodes. No CARP election is used.
See the active-active guide for health-probe limitations and failure tests.

## Image and bootstrap changes

- Marketplace identity: `freebsd:freebsd-15_1:15_1-release-amd64-gen2-zfs:<revision>`.
  Publisher and SKU were checked against the public Marketplace catalog; regional/subscription availability still needs Azure validation.
- Bicep compiles to the committed `ARM/main.json`; both UI copies and parameter files are kept in sync.
- The extension runs an embedded shell launcher with `fileUris: []`; FreeBSD `fetch` downloads the entry point.
  This avoids the old handler's Python `rU`/DOS-to-Unix preprocessing failure on Python 3.11 and newer.
- The official bootstrap is pinned to a commit and verified with SHA-256. Its `set -e` and FreeBSD pkgbase handling remain enabled.
  Its core-source archive is also pinned. Only the final reboot is suppressed, until Azure integration is installed.
- Azure Agent comes from OPNsense's `azure-agent` package, with its FreeBSD service paths and Python dependencies.
  The `WALinuxVersion` field is now a **minimum package version**, not a source-archive selector.
- Parameters travel as base64 JSON; optional empty subnet arguments cannot shift into the wrong position.
- Configuration keeps its original XML version so OPNsense runs its own migrations. There is no fake Windows subnet when Windows is disabled.
- Conversion failures stop provisioning. A partial conversion is not automatically attempted again.
  Successful retries skip destructive reinstallation; this is **not** an in-place firmware upgrade mechanism.
- Separate startup hooks preserve the inherited Azure platform-IP workaround, start the agent and run local checks.
  Vendor-owned OPNsense startup hooks are not edited.
- Boot diagnostics, stage status, logs and installed-version records support the first Azure test.

## Repeating a test

`latest` means the latest revision of **FreeBSD 15.1**, while series `26.7` installs the maintenance packages
currently published by OPNsense. This is a repeatable provisioning procedure, **not a bit-for-bit frozen image**.
For an exact comparison, record the Git commit, resolved Azure image revision and installed package versions.
Set `FreeBSDImageVersion` to that numeric revision and `OpnScriptURI` to this fork's commit SHA for subsequent runs.
A frozen OPNsense package set would additionally require a retained repository snapshot or a validated custom image.

## Local validation

Use Bicep CLI **0.47.16**:

```sh
bicep build bicep/main.bicep --outfile ARM/main.json
cp bicep/main.parameters.json ARM/main.parameters.json
cp bicep/uiFormDefinition.json ARM/uiFormDefinition.json
sh -n scripts/launch-bootstrap.sh
sh -n scripts/configureopnsense.sh
sh -n scripts/verify_opnsense.sh
python3 -m unittest discover -s tests -v
```

Regression tests also reproduce the legacy handler's `rU` failure and exercise the ARM-embedded launcher
with mocked downloads, including failed/empty downloads and bootstrap exit-code propagation.

The OPNsense deployment validation workflow runs only local checks and does not authenticate to Azure or deploy resources.
The inherited deployment-checker workflows are legacy, manual-only workflows and are not the supported test path
for this branch; use the portal button and guide above.

## Upstream and sources

Original architecture, configuration and contributions: [dmauser/opnazure](https://github.com/dmauser/opnazure).
See [LICENSE](LICENSE). The bootstrap fixture retains the upstream BSD copyright/license.

- [FreeBSD 15.1 Marketplace listing](https://marketplace.microsoft.com/en-us/product/freebsd.freebsd-15_1?tab=Overview)
- [OPNsense 26.7 release notes](https://docs.opnsense.org/releases/CE_26.7.html)
- [Pinned official bootstrap](https://github.com/opnsense/update/blob/db018c35aac47020c69dc507c3ae67a30dbdf2ab/src/bootstrap/opnsense-bootstrap.sh.in)
- [Pinned core source](https://github.com/opnsense/core/tree/791286d4dec8ffeba3841901ff91129c63db0af8)
