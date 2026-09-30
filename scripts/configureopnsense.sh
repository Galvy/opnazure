#!/bin/sh
# Fresh Azure FreeBSD 15.1 -> OPNsense 26.7. Never use as an in-place upgrade.
set -eu
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH
umask 077

[ "$(id -u)" = 0 ] || { echo 'Must run as root' >&2; exit 1; }
[ "$(uname -s)" = FreeBSD ] || { echo 'Must run on FreeBSD' >&2; exit 1; }
[ "$#" = 1 ] || { echo 'Expected base64-encoded JSON settings' >&2; exit 1; }

WORK=/var/db/opnazure
mkdir -p "$WORK"
# A retry after conversion must never delete an installed firewall's packages/config.
if [ -f "$WORK/bootstrap-complete" ]; then
    echo "Bootstrap already completed. See $WORK/status and /var/log/opnazure-bootstrap.log."
    exit 0
fi
if [ -f "$WORK/conversion-started" ] || [ -f /usr/local/opnsense/version/core ]; then
    echo "Refusing to re-bootstrap a converted/partially converted VM. Inspect logs; use a fresh test VM." >&2
    exit 1
fi
case "$(freebsd-version -u)" in
    15.1-RELEASE*) ;;
    *) echo 'Expected a FreeBSD 15.1-RELEASE image' >&2; exit 1 ;;
esac
[ "$(uname -p)" = amd64 ] || { echo 'Expected amd64' >&2; exit 1; }
if ! mkdir "$WORK/lock"; then
    echo 'Another bootstrap is running (or a previous run was interrupted). Inspect the VM.' >&2
    exit 1
fi
STAGE=preflight
cleanup() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        printf 'failed: %s (exit %s)\n' "$STAGE" "$result" > "$WORK/status"
        echo "ERROR during $STAGE; no automatic retry or reboot."
    fi
    rmdir "$WORK/lock"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
printf 'Provisioning log: /var/log/opnazure-bootstrap.log\n'
exec >> /var/log/opnazure-bootstrap.log 2>&1
log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
stage() { STAGE=$1; printf '%s\n' "$STAGE" > "$WORK/status"; log "$STAGE"; }
fetch_file() {
    url=$1
    target=$2
    # The standalone HA template embeds its artifacts. Only external upstream
    # bootstrap/packages are downloaded; retain URL fetching for older scenarios.
    if [ -n "${OPNAZURE_SOURCE:-}" ] && [ -n "${SCRIPT_URI:-}" ]; then
        case "$url" in
            "$SCRIPT_URI"*)
                relative=${url#"$SCRIPT_URI"}
                if [ -f "$OPNAZURE_SOURCE/$relative" ]; then
                    cp "$OPNAZURE_SOURCE/$relative" "$target"
                    return
                fi
                ;;
        esac
    fi
    attempt=1
    while ! fetch -T 60 -o "${target}.download" "$url"; do
        rm -f "${target}.download"
        [ "$attempt" -lt 3 ] || return 1
        attempt=$((attempt + 1))
        sleep 5
    done
    mv "${target}.download" "$target"
}

stage preflight
PYTHON=
for candidate in /usr/local/bin/python3 /usr/local/bin/python3.[0-9]*; do
    if [ -x "$candidate" ] && "$candidate" -c 'import json, ipaddress, xml.etree.ElementTree' 2>/dev/null; then
        PYTHON=$candidate
        break
    fi
done
[ -n "$PYTHON" ] || { log 'No usable Python 3 in the Azure image'; exit 1; }
# Decode without eval/source or interpolating user input into shell code.
"$PYTHON" -c 'import base64,json,sys; print(json.dumps(json.loads(base64.b64decode(sys.argv[1],validate=True))))' "$1" > "$WORK/settings.json"
SCRIPT_URI=$("$PYTHON" -c 'import json,re,sys; u=json.load(open(sys.argv[1]))["scriptURI"]; assert re.fullmatch(r"https://[A-Za-z0-9._~:/%+-]+/",u), "Invalid script URI"; print(u)' "$WORK/settings.json")
HA_ENABLED=$("$PYTHON" -c 'import json,sys; print("yes" if json.load(open(sys.argv[1])).get("ha") else "no")' "$WORK/settings.json")
CONFIG_FILES="config.xml config-active-active-primary.xml config-active-active-secondary.xml"
if [ "$HA_ENABLED" = yes ]; then CONFIG_FILES=config.xml; fi
for file in $CONFIG_FILES get_nic_gw.py prepare_config.py actions_waagent.conf verify_opnsense.sh; do
    fetch_file "${SCRIPT_URI}${file}" "$WORK/$file"
done
BOOTSTRAP_COMMIT=db018c35aac47020c69dc507c3ae67a30dbdf2ab
fetch_file "https://raw.githubusercontent.com/opnsense/update/${BOOTSTRAP_COMMIT}/src/bootstrap/opnsense-bootstrap.sh.in" "$WORK/bootstrap.upstream.sh"
"$PYTHON" "$WORK/prepare_config.py" "$WORK"
sha256 "$WORK/settings.json" "$WORK/config.rendered.xml" "$WORK/prepare_config.py" "$WORK/get_nic_gw.py" "$WORK/bootstrap.upstream.sh" > "$WORK/input-checksums.txt"
AGENT_MINIMUM=$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["agentMinimumVersion"])' "$WORK/settings.json")
# Record the image selected by Azure if IMDS is available, without querying identities.
if fetch -T 10 -H 'Metadata: true' -o "$WORK/image-metadata.json" 'http://169.254.169.254/metadata/instance/compute/storageProfile/imageReference?api-version=2021-02-01&format=json'; then
    log 'Recorded image reference from Azure IMDS'
else
    log 'Image metadata unavailable; obtain exactVersion from the Azure VM resource'
fi

stage conversion
cp "$WORK/config.rendered.xml" /usr/local/etc/config.xml
touch "$WORK/conversion-started"
# The pinned upstream script retains set -e, including its FreeBSD pkgbase handling.
# Only its final reboot is suppressed and its core source is pinned.
sh "$WORK/bootstrap.sh" -y -r 26.7

stage azure-integration
# Use the FreeBSD-port package (correct service paths, Python and dependencies).
pkg install -y azure-agent bash os-frr
AGENT_VERSION=$(pkg query '%v' azure-agent)
[ "$(pkg version -t "$AGENT_VERSION" "$AGENT_MINIMUM")" != '<' ] || {
    log "azure-agent $AGENT_VERSION is older than required $AGENT_MINIMUM"; exit 1;
}
# Do not stop/restart the extension's parent agent while provisioning is running.
# Disable reprovisioning and scratch-disk changes on subsequent OPNsense boots.
[ -f /usr/local/etc/waagent.conf ] || cp /usr/local/etc/waagent.conf.sample /usr/local/etc/waagent.conf
for setting in 'ResourceDisk.EnableSwap=n' 'ResourceDisk.Format=n' 'Provisioning.Agent=disabled' 'Provisioning.DeleteRootPassword=n' 'Provisioning.MonitorHostName=n'; do
    key=${setting%%=*}
    sed -i '' "/^${key}=/d" /usr/local/etc/waagent.conf
    printf '%s\n' "$setting" >> /usr/local/etc/waagent.conf
done
sysrc waagent_enable=YES
# A separate late hook starts waagent after the Azure platform route is restored.
sysrc waagent_skip=YES
install -m 644 "$WORK/actions_waagent.conf" /usr/local/opnsense/service/conf/actions.d/actions_waagent.conf
install -m 755 "$WORK/verify_opnsense.sh" /usr/local/sbin/opnazure-verify

# Preserve the upstream platform-IP workaround, without modifying vendor hooks.
# 168.63.129.16 is Azure's wire server/probe IP; IMDS is 169.254.169.254.
cat > /usr/local/etc/rc.syshook.d/start/21-opnazure-platform <<'HOOK'
#!/bin/sh
set -eu
# A missing host route is normal on some image/lease combinations.
route delete -host 168.63.129.16 >/dev/null 2>&1 || :
arp -s 168.63.129.16 12:34:56:78:9a:bc
service waagent onestatus >/dev/null 2>&1 || service waagent start
HOOK
chmod 755 /usr/local/etc/rc.syshook.d/start/21-opnazure-platform

cat > /usr/local/etc/rc.syshook.d/start/95-opnazure-verify <<'HOOK'
#!/bin/sh
set -eu
exec >> /var/log/opnazure-firstboot.log 2>&1
WORK=/var/db/opnazure
printf 'verifying-first-boot\n' > "$WORK/status"
trap 'printf "first-boot-failed\n" > /var/db/opnazure/status' EXIT
# Retry on a later boot if renewal fails; do not remove the marker prematurely.
if [ ! -f "$WORK/certificate-renewed" ]; then
    configctl webgui restart renew
    touch "$WORK/certificate-renewed"
fi
/usr/local/sbin/opnazure-verify
printf 'ready\n' > "$WORK/status"
date -u > "$WORK/first-boot-complete"
trap - EXIT
HOOK
chmod 755 /usr/local/etc/rc.syshook.d/start/95-opnazure-verify

# HA is optional; existing scenarios retain their original bootstrap.
HA_ENABLED=$("$PYTHON" -c 'import json,sys; print("yes" if json.load(open(sys.argv[1])).get("ha") else "no")' "$WORK/settings.json")
if [ "$HA_ENABLED" = yes ]; then
    stage installing-active-backup
    mkdir -p "$WORK/ha"
    for file in agent.py install.py; do
        fetch_file "${SCRIPT_URI}ha/$file" "$WORK/ha/$file"
    done
    /usr/local/bin/python3 "$WORK/ha/install.py" "$WORK/settings.json" "$WORK/ha/agent.py"
fi

stage checking-installed-packages
OPN_VERSION=$(pkg query '%v' opnsense)
case "$OPN_VERSION" in 26.7*) ;; *) log "Unexpected OPNsense version: $OPN_VERSION"; exit 1 ;; esac
[ "$(pkg version -t "$OPN_VERSION" '26.7.4_1')" != '<' ] || {
    log "OPNsense $OPN_VERSION is older than the minimum 26.7.4_1; check the mirror"; exit 1;
}
pkg check -d -a
/usr/local/sbin/waagent -version
{
    printf 'OPNsense=%s\nAzureAgent=%s\nBootstrapCommit=%s\n' "$OPN_VERSION" "$AGENT_VERSION" "$BOOTSTRAP_COMMIT"
    freebsd-version -u
    pkg query '%n %v'
} > "$WORK/installed-versions.txt"
# Leave time for the extension to report its exit status, AFTER every install/check.
stage awaiting-reboot
touch "$WORK/bootstrap-complete"
if ! shutdown -r +1 'OPNsense bootstrap completed; rebooting into OPNsense'; then
    rm -f "$WORK/bootstrap-complete"
    exit 1
fi
log 'Bootstrap completed. First-boot checks are still pending; see /var/db/opnazure/status after reboot.'
