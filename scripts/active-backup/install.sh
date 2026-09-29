#!/bin/sh
# Called after OPNsense bootstrap. Configuration lives under /conf and survives upgrades.
set -eu
BASE_URI="$1"
CONFIG_B64="$2"
install -d -m 700 /conf/opnazure-ha
fetch -q -o /conf/opnazure-ha/agent.py "${BASE_URI}active-backup/agent.py"
fetch -q -o /conf/opnazure-ha/configure.py "${BASE_URI}active-backup/configure.py"
printf '%s' "$CONFIG_B64" | /usr/local/bin/python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read(), validate=True))' > /conf/opnazure-ha/azure.json
chmod 600 /conf/opnazure-ha/azure.json
/usr/local/bin/python3 /conf/opnazure-ha/configure.py
# Persistent boot hook; daemon restarts after process crashes, but health stays fail-closed.
cat > /conf/opnazure-ha/start <<'EOF'
#!/bin/sh
/usr/sbin/daemon -r -R 5 -P /var/run/opnazure-ha-supervisor.pid -p /var/run/opnazure-ha.pid \
    -o /var/log/opnazure-ha.log /usr/local/bin/python3 /conf/opnazure-ha/agent.py
EOF
chmod 700 /conf/opnazure-ha/start
cat > /usr/local/etc/rc.syshook.d/start/95-opnazure-ha <<'EOF'
#!/bin/sh
/conf/opnazure-ha/start
EOF
chmod 700 /usr/local/etc/rc.syshook.d/start/95-opnazure-ha
# No arming here: configure and test the real OpenVPN instance on each node first.
