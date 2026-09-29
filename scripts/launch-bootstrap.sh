#!/bin/sh
# Embedded in commandToExecute by Bicep; not downloaded through fileUris.
# CustomScriptForLinux 1.5 uses obsolete Python rU mode to preprocess .sh files.
# All values arrive as base64 arguments; never interpolate a URL into shell code.
set -eu
umask 077
[ "$#" = 2 ] || { echo "Expected settings and script URL as base64 arguments" >&2; exit 1; }
script_uri=$(printf %s "$2" | /usr/bin/base64 -d)
case "$script_uri" in
    https://*) ;;
    *) echo "Bootstrap URL must use HTTPS" >&2; exit 1 ;;
esac
work=$(mktemp -d /var/tmp/opnazure-launch.XXXXXX)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT
trap "exit 1" HUP INT TERM
attempt=1
while ! fetch -T 60 -o "$work/configureopnsense.sh.download" "$script_uri"; do
    rm -f "$work/configureopnsense.sh.download"
    [ "$attempt" -lt 3 ] || { echo "Bootstrap download failed after 3 attempts" >&2; exit 1; }
    attempt=$((attempt + 1))
    sleep 5
done
[ -s "$work/configureopnsense.sh.download" ] || { echo "Bootstrap download is empty" >&2; exit 1; }
mv "$work/configureopnsense.sh.download" "$work/configureopnsense.sh"
# Keep this process alive so its exit status reports bootstrap success/failure.
/bin/sh "$work/configureopnsense.sh" "$1"
