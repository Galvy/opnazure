# OPNsense 26.7 on Azure — TwoNics test branch

This branch of [Galvy/opnazure](https://github.com/Galvy/opnazure/tree/update/freebsd15-opnsense26.7)
prepares a fresh **FreeBSD 15.1 AMD64 Gen2 ZFS VM → latest OPNsense 26.7 maintenance release**.
It is based on [dmauser/opnazure](https://github.com/dmauser/opnazure).

**Status: prepared for an Azure test; no real Azure deployment has been performed for this change.**
Local checks cover template compilation, artifact consistency, configuration preparation and
bootstrap failure/reboot behaviour with mocked FreeBSD commands. They do not prove Azure runtime compatibility.
This branch supports **TwoNics only**. It does not implement HA or configure an OpenVPN server.

## Deploy this fork and branch

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
4. Keep OPNsense series **26.7**, image revision **latest**, bootstrap filename and fork script URL at their defaults.
5. Select an available x64 VM size supporting two NICs. The default is `Standard_B2s` (4 GiB); a larger size can help the first test.
6. Review Marketplace terms and the cost estimate, then deploy when ready.
7. Wait for the extension **and the subsequent reboot**, then visit `https://<public-IP>`.
   Initial credentials are inherited from upstream: **root / opnsense**. Change the password on first login.
8. Follow the [test and troubleshooting guide](docs/twonics-test.md) before considering the deployment successful.

Opening the button does not deploy anything until you submit the Azure wizard. No GitHub `AZURE_CREDENTIALS`
secret is needed for a portal deployment; Azure uses your signed-in account.

## Image and bootstrap changes

- Marketplace identity: `freebsd:freebsd-15_1:15_1-release-amd64-gen2-zfs:<revision>`.
  Publisher and SKU were checked against the public Marketplace catalog; regional/subscription availability still needs Azure validation.
- Bicep compiles to the committed `ARM/main.json`; both UI copies and parameter files are kept in sync.
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
sh -n scripts/configureopnsense.sh
sh -n scripts/verify_twonic.sh
python3 -m unittest discover -s tests -v
```

The TwoNics validation workflow runs only local checks and does not authenticate to Azure or deploy resources.
The inherited deployment-checker workflows are legacy, manual-only workflows and are not the supported test path
for this branch; use the portal button and guide above.

## Upstream and sources

Original architecture, configuration and contributions: [dmauser/opnazure](https://github.com/dmauser/opnazure).
See [LICENSE](LICENSE). The bootstrap fixture retains the upstream BSD copyright/license.

- [FreeBSD 15.1 Marketplace listing](https://marketplace.microsoft.com/en-us/product/freebsd.freebsd-15_1?tab=Overview)
- [OPNsense 26.7 release notes](https://docs.opnsense.org/releases/CE_26.7.html)
- [Pinned official bootstrap](https://github.com/opnsense/update/blob/db018c35aac47020c69dc507c3ae67a30dbdf2ab/src/bootstrap/opnsense-bootstrap.sh.in)
- [Pinned core source](https://github.com/opnsense/core/tree/791286d4dec8ffeba3841901ff91129c63db0af8)
