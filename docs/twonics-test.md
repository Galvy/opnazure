# First Azure test: FreeBSD 15.1 → OPNsense 26.7

## Scope and known limits

Use the button in the **test branch README**, a fresh Resource Group and a new VM.
The template does not upgrade existing firewalls. The first single-node test should use `TwoNics`,
a new VNet and `DeployWindows=false`. For the two-node scenario, see the
[Active-Active guide](active-active-test.md). Active-backup and OpenVPN server configuration
are outside this branch.

The image identity is:

```text
freebsd:freebsd-15_1:15_1-release-amd64-gen2-zfs:latest
```

Publisher `freebsd`, offer and AMD64 Gen2 ZFS plan were verified on 2026-09-29 using
[the public Marketplace catalog](https://catalogapi.azure.com/offers/freebsd.freebsd-15_1?api-version=2018-08-01-beta).
The first reported TwoNics Azure attempt reached the guest extension but failed in legacy
script preprocessing before the bootstrap ran. The inline-launcher correction has not yet
been retested on Azure. Availability, quotas,
Marketplace eligibility, CustomScriptForLinux 1.5 compatibility, Azure Agent reporting,
configuration migrations and actual forwarding must be confirmed in your Azure test.

## Optional image preflight in Azure Cloud Shell

These commands are for you to run in the intended subscription. They do not create a VM:

```sh
az vm image show --location westeurope \
  --urn freebsd:freebsd-15_1:15_1-release-amd64-gen2-zfs:latest \
  --query '{name:name,plan:plan,architecture:architecture,hyperVGeneration:hyperVGeneration}'
az vm image terms show \
  --urn freebsd:freebsd-15_1:15_1-release-amd64-gen2-zfs:latest
```

Replace `westeurope` with the selected region. If Azure requires Marketplace acceptance,
review and accept it in the portal, or explicitly use `az vm image terms accept` for that URN.
`FreeBSDImageVersion` accepts an exact numeric image revision if you want to pin one.

## Portal inputs

- Use the fork/branch button in the README. It loads template and UI from `Galvy/opnazure`.
- Keep the scripts URL pointing at `Galvy/opnazure/update/freebsd15-opnsense26.7/scripts/`.
- Set your management **public IPv4 CIDR**. The default sample parameters contain a documentation-only address.
  NSG access is restricted to that CIDR; using a VPN/proxy may change your public source address.
- Use an x64 size with at least two NICs and enough memory (default B2s has 4 GiB).
- An existing VNet, if selected, must be in the deployment Resource Group with both subnet names supplied.
- Initial OPNsense credentials remain `root` / `opnsense`; change them at first login.

The branch creates no VPN listener/rule. Later OpenVPN testing will need both OPNsense
configuration and a matching Azure NSG inbound rule, in addition to client return routes.

## What a successful test means

Azure extension success means the pre-reboot provisioning commands finished.
It does **not** certify that the firewall booted, migrated configuration or forwards traffic.
Allow for the scheduled reboot after extension completion, then check:

1. Azure **Boot diagnostics / Serial Console**: the VM boots OPNsense without a kernel panic or boot loop.
2. Azure **VM agent status**: Ready, and CustomScript extension reports Succeeded.
3. From your allowed public address: `https://<public-IP>` loads, login works, and a fresh certificate is present.
4. GUI firmware information: OPNsense **26.7.4_1 or newer within 26.7**, FreeBSD **15.1**.
5. Interfaces: WAN `hn0` and LAN `hn1` have the private IPs shown on the two Azure NICs.
6. Run the local guest checks from the serial console or an allowed internal SSH connection:

```sh
cat /var/db/opnazure/status
cat /var/db/opnazure/installed-versions.txt
/usr/local/sbin/opnazure-verify
```

Expected status after startup: `ready`. This is a set of local checks, not an end-to-end connectivity guarantee.
SSH from the Internet also requires an OPNsense WAN rule; the NSG alone does not grant it.

7. Use a separate test client subnet. Associate a route table whose `0.0.0.0/0` next hop is the
   OPNsense **trusted NIC private IP**. Add the corresponding LAN firewall permission,
   outbound NAT and a return route via the Azure LAN gateway for that client subnet.
   Test DNS, HTTPS to the Internet and access to the intended private destinations.
   Check NIC effective routes/packet captures to ensure traffic really passes through OPNsense.
8. Reboot the firewall once more. Repeat GUI, agent and routed-client checks.

The inherited optional Windows deployment creates a route table but does **not** associate
it automatically with the management subnet. If you enable it for a later test, verify and
associate the table yourself before relying on a forwarding result. Its existing Windows
image/size options are outside this FreeBSD/OPNsense update and need regional validation.

## CustomScript failure: `invalid mode: 'rU'`

The first Azure test reported this failure in
`Microsoft.OSTCExtensions.CustomScriptForLinux-1.5.4/customscript.py`:

```text
preprocess_files -> dos2unix -> open(file_path, 'rU')
ValueError: invalid mode: 'rU'
```

The handler downloaded the file and then failed while preparing it. The bootstrap had not
started in this attempt. Python 3.11 removed the obsolete `U` file mode; changing the GitHub
URL or retrying the same extension settings cannot fix that code path.

The template now sets `fileUris: []` and embeds `scripts/launch-bootstrap.sh` in
`commandToExecute`. The legacy handler supports inline commands without downloaded files.
The launcher uses FreeBSD `fetch` over HTTPS to download the bootstrap into a private
working directory, retries failed downloads, rejects empty files and propagates the
bootstrap exit status. Inputs remain base64-encoded arguments, not interpolated shell code.
Shell files are committed with LF line endings, so the handler does not need to normalize them.
This correction is shared by TwoNics and Active-Active.

To test the correction, reopen **Deploy to Azure from the updated branch README**.
Do not use the old deployment's unchanged template/Retry action: that definition still contains
`fileUris` and the failing download/preprocessing path. The new extension settings change the
command and file list, allowing Azure to process a new configuration.

For a clean comparison, use a fresh test Resource Group. Reusing the same VM is only appropriate
if you confirm the failure was this pre-bootstrap error, with no prior partial conversion;
keep the original deployment parameters and submit the updated template. If any conversion
has already started, follow the existing partial-conversion guidance below instead.

The old handler may log `fileUris value provided is empty or invalid. Continue with executing
command...` for an inline command. That message alone is not a deployment failure; check the
final command/extension status. The launcher is part of the compiled ARM template: editing
only the downloaded bootstrap cannot correct a template that still uses the old file list.

Sources:
- [Python 3.11 file-mode change](https://docs.python.org/3.11/library/functions.html#open)
- [Legacy CustomScript inline-command support](https://github.com/Azure/azure-linux-extensions/blob/ce24c534872610dab4a802735bb64056d81d700f/CustomScript/README.md)
- [Handler download and preprocessing code](https://github.com/Azure/azure-linux-extensions/blob/ce24c534872610dab4a802735bb64056d81d700f/CustomScript/customscript.py)

## Diagnostics

| File | Meaning |
|---|---|
| `/var/log/opnazure-bootstrap.log` | Full preparation, bootstrap and package-install output |
| `/var/log/opnazure-firstboot.log` | Certificate renewal and local startup checks |
| `/var/db/opnazure/status` | Current stage, failure, awaiting reboot, or ready |
| `/var/db/opnazure/bootstrap-complete` | Pre-reboot commands finished; not a health signal |
| `/var/db/opnazure/first-boot-complete` | Timestamp of the last successful startup check |
| `/var/db/opnazure/settings.json` | Actual provisioning inputs |
| `/var/db/opnazure/installed-versions.txt` | Package versions and bootstrap commit |
| `/var/db/opnazure/input-checksums.txt` | Downloaded configuration/helper/bootstrap checksums |
| `/var/db/opnazure/image-metadata.json` | Image reference from IMDS, when available |

Also inspect `/var/log/waagent.log` and the CustomScript logs under `/var/log/azure`.
Azure Boot diagnostics is enabled with managed storage. If Serial Console is unavailable,
use Boot diagnostics and the platform's guest-access/recovery facilities.

The startup hook preserves the upstream static ARP workaround for **168.63.129.16**
(Azure wire server and probe source). IMDS uses **169.254.169.254**, a different address.
Confirm the wire server remains reachable and that agent status returns to Ready after reboot.

If provisioning stops after `conversion-started`, preserve the logs and use a **fresh test VM**.
Do not clear the marker and rerun a destructive conversion on the partially converted firewall.
A failure in the final reboot scheduling is also reported as a failure, not success.

## Reproducibility and evidence

Record the repository commit and Azure deployment inputs. Retrieve the resolved image revision:

```sh
az vm show --resource-group <test-rg> --name <vm-name> \
  --query storageProfile.imageReference
```

For another run using the same source, set the scripts URL to
`https://raw.githubusercontent.com/Galvy/opnazure/<commit-SHA>/scripts/` and use the resolved
numeric `FreeBSDImageVersion`. Retain the installed package-version list.
The bootstrap and core archive are pinned; the official OPNsense 26.7 repository supplies
current maintenance packages, so deployments on different dates can install newer packages.
The initial minimum expected version is 26.7.4_1; this branch never automatically moves to 27.1.

Local verification before publication includes Bicep compilation, shell syntax/lint,
XML/configuration tests, fork-link checks and mocked orchestration failures.
Real Azure deployment and network tests are intentionally left for the manual test above.
