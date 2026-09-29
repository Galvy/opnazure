#!/bin/sh
# Read-only guest checks; no claim of end-to-end VPN or client connectivity.
set -eu
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin
export PATH
case "$(opnsense-version -v)" in
    26.7*) ;;
    *) echo 'Unexpected OPNsense release' >&2; exit 1 ;;
esac
case "$(freebsd-version -u)" in
    15.1-RELEASE*) ;;
    *) echo 'Unexpected FreeBSD userspace' >&2; exit 1 ;;
esac
case "$(freebsd-version -r)" in
    15.1-RELEASE*) ;;
    *) echo 'Unexpected running FreeBSD kernel' >&2; exit 1 ;;
esac
for nic in hn0 hn1; do
    ifconfig "$nic" | grep -q 'inet '
done
route -n get default
service waagent onestatus
/usr/local/sbin/waagent -version
# Verify that the agent's platform endpoint is reachable, not only its process.
fetch -T 15 -o /dev/null 'http://168.63.129.16/?comp=versions'
pkg check -d -a
pgrep -x configd >/dev/null || pgrep -f '/usr/local/opnsense/service/configd.py' >/dev/null
sockstat -4 -l | awk '$6 ~ /:443$/ { found=1 } END { exit !found }'
pfctl -s info | grep -q 'Status: Enabled'
opnsense-version -a
freebsd-version -kru
printf 'Local TwoNics checks passed. Verify HTTPS and routed client traffic externally.\n'
