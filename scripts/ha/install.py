#!/usr/local/bin/python3
"""Install the optional HA controller after OPNsense conversion, before reboot."""
import json
import os
from pathlib import Path
import shutil
import sys


def install(settings, agent, prefix=Path('/')):
    config = settings['ha']
    if config['node'] not in ('Primary', 'Secondary') or config['probe_port'] != 8080:
        raise ValueError('Invalid HA role or probe port')
    root = prefix / 'conf/opnazure-ha'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    target = root / 'azure.json'
    target.write_text(json.dumps(config, indent=2) + '\n')
    target.chmod(0o600)
    program = prefix / 'usr/local/sbin/opnazure-ha'
    program.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(agent, program)
    program.chmod(0o700)
    hooks = prefix / 'usr/local/etc/rc.syshook.d'
    for kind in ('early', 'start'):
        (hooks / kind).mkdir(parents=True, exist_ok=True)
    early = hooks / 'early/01-opnazure-ha'
    early.write_text('''#!/bin/sh
# Never reuse readiness from a previous boot.
rm -f /var/db/opnazure/first-boot-complete
''')
    start = hooks / 'start/96-opnazure-ha'
    start.write_text('''#!/bin/sh
# No automatic respawn: a failed node requires operator recovery.
/usr/sbin/daemon -f -p /var/run/opnazure-ha.pid -o /var/log/opnazure-ha.log /usr/local/bin/python3 /usr/local/sbin/opnazure-ha
''')
    early.chmod(0o755)
    start.chmod(0o755)


if __name__ == '__main__':
    install(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
