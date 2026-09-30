#!/bin/sh
set -eu
umask 077
[ "$#" = 2 ] || exit 1
OPNAZURE_SOURCE=$(mktemp -d /var/tmp/opnazure-source.XXXXXX)
export OPNAZURE_SOURCE
cleanup() { rm -rf "$OPNAZURE_SOURCE"; }
trap cleanup EXIT
trap "exit 1" HUP INT TERM
mkdir -p "$OPNAZURE_SOURCE/ha"
PYTHON=
for candidate in /usr/local/bin/python3 /usr/local/bin/python3.[0-9]*; do
    if [ -x "$candidate" ] && "$candidate" -c "import base64,json,pathlib"; then
        PYTHON=$candidate
        break
    fi
done
[ -n "$PYTHON" ] || exit 1
"$PYTHON" -c "import base64,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); data=json.loads(base64.b64decode(sys.argv[2],validate=True)); assert all(not pathlib.Path(k).is_absolute() and all(x != chr(46)*2 for x in pathlib.Path(k).parts) for k in data); [p.joinpath(k).write_bytes(base64.b64decode(v,validate=True)) for k,v in data.items()]" "$OPNAZURE_SOURCE" "$2"
/bin/sh "$OPNAZURE_SOURCE/configureopnsense.sh" "$1"
