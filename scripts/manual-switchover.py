#!/usr/bin/env python3
"""Operator-run planned maintenance, using the operator's Azure CLI session.

No daemon, credentials, lease or automatic recovery. Run one operation at a time
from one console. See docs/active-backup.md before using switch/start-standby.
"""
import argparse
import json
import subprocess
import sys

NODES = ('Primary', 'Secondary')
RULES = ('Role-Probe', 'Workload-Inbound', 'Workload-Outbound')
OFF = ('PowerState/stopped', 'PowerState/deallocated')


class Azure:
    def __init__(self, subscription, group, cluster):
        self.subscription, self.group, self.cluster = subscription, group, cluster

    def call(self, *args):
        result = subprocess.run(
            ['az', *args, '--subscription', self.subscription, '--resource-group', self.group,
             '--only-show-errors', '--output', 'json'],
            check=True, capture_output=True, text=True, timeout=900)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def power(self, node):
        data = self.call('vm', 'get-instance-view', '--name', f'{self.cluster}-{node}')
        return next((s['code'] for s in data['instanceView']['statuses']
                     if s['code'].startswith('PowerState/')), 'unknown')

    def rule(self, node, name):
        rule = self.call('network', 'nsg', 'rule', 'show',
                         '--nsg-name', f'{self.cluster}-{node}-Gate', '--name', name)
        expected = {'priority': 120 if name == 'Role-Probe' else 200,
                    'direction': 'Outbound' if name == 'Workload-Outbound' else 'Inbound',
                    'protocol': 'Tcp' if name == 'Role-Probe' else '*',
                    'sourceAddressPrefix': 'AzureLoadBalancer' if name == 'Role-Probe' else '*',
                    'destinationAddressPrefix': '*', 'sourcePortRange': '*',
                    'destinationPortRange': '443' if name == 'Role-Probe' else '*'}
        if any(rule.get(k) != v for k, v in expected.items()):
            raise RuntimeError(f'{node}/{name}: rule differs from the manual template; inspect before proceeding')
        if rule.get('provisioningState') != 'Succeeded' or rule.get('access') not in ('Allow', 'Deny'):
            raise RuntimeError(f'{node}/{name}: NSG update is incomplete or invalid')
        return rule['access']

    def set_rule(self, node, name, access):
        self.call('network', 'nsg', 'rule', 'update',
                  '--nsg-name', f'{self.cluster}-{node}-Gate', '--name', name, '--access', access)
        # Azure CLI waits for the write; still confirm the actual resulting rule.
        if self.rule(node, name) != access:
            raise RuntimeError(f'{node}/{name}: requested access was not applied')

    def stop(self, node):
        if self.power(node) not in OFF:
            # Planned shutdown; no force-deallocate, skipShutdown or no-wait.
            self.call('vm', 'deallocate', '--name', f'{self.cluster}-{node}')
        if self.power(node) not in OFF:
            raise RuntimeError(f'{node}: shutdown not confirmed; target must remain isolated')

    def start(self, node):
        self.call('vm', 'start', '--name', f'{self.cluster}-{node}')
        if self.power(node) != 'PowerState/running':
            raise RuntimeError(f'{node}: startup not confirmed')


def snapshot(azure):
    return {node: {'power': azure.power(node),
                   'rules': {rule: azure.rule(node, rule) for rule in RULES}}
            for node in NODES}


def all_access(state, access):
    return all(state['rules'][rule] == access for rule in RULES)


def switch(azure, target):
    if target not in NODES:
        raise ValueError('Unknown target node')
    peer = next(node for node in NODES if node != target)
    state = snapshot(azure)  # Read/validate both nodes before any mutation.
    if state[target]['power'] != 'PowerState/running':
        raise RuntimeError('Target must be running, upgraded and checked before switchover')
    if all_access(state[target], 'Allow') and all_access(state[peer], 'Deny'):
        return f'{target} is already selected; no changes made'
    # NSG deny does not end established sessions: stopping the peer is mandatory.
    azure.set_rule(peer, 'Role-Probe', 'Deny')
    azure.stop(peer)
    azure.set_rule(peer, 'Workload-Inbound', 'Deny')
    azure.set_rule(peer, 'Workload-Outbound', 'Deny')
    if azure.power(peer) not in OFF or any(azure.rule(peer, rule) != 'Deny' for rule in RULES):
        raise RuntimeError('Peer is not stopped and isolated; refusing promotion')
    azure.set_rule(target, 'Workload-Outbound', 'Allow')
    azure.set_rule(target, 'Workload-Inbound', 'Allow')
    azure.set_rule(target, 'Role-Probe', 'Allow')
    return (f'{target} selected; {peer} stopped and isolated. '
            'Wait for both LB probes and verify VPN/workload traffic. No automatic rollback.')


def start_standby(azure, node):
    if node not in NODES:
        raise ValueError('Unknown standby node')
    state = snapshot(azure)
    peer = next(other for other in NODES if other != node)
    if not all_access(state[node], 'Deny'):
        raise RuntimeError('Refusing startup: standby workload and probe rules must all be Deny')
    if not all_access(state[peer], 'Allow') or state[peer]['power'] != 'PowerState/running':
        raise RuntimeError('Verify a running selected peer before restarting standby')
    if state[node]['power'] == 'PowerState/running':
        return f'{node} already running and isolated; no changes made'
    if state[node]['power'] not in OFF:
        raise RuntimeError('Standby power state is unknown or an operation is in progress')
    azure.start(node)
    return f'{node} started with workload/probe gates closed; verify GUI and perform maintenance'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'switch', 'start-standby'))
    parser.add_argument('--subscription', required=True)
    parser.add_argument('--resource-group', required=True)
    parser.add_argument('--cluster', default='opn-vpn')
    parser.add_argument('--node', choices=NODES)
    args = parser.parse_args()
    if args.command != 'status' and not args.node:
        parser.error('--node is required for switch/start-standby')
    azure = Azure(args.subscription, args.resource_group, args.cluster)
    try:
        if args.command == 'status':
            print(json.dumps(snapshot(azure), indent=2))
        else:
            print(switch(azure, args.node) if args.command == 'switch' else start_standby(azure, args.node))
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f'Stopped: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        print('No automatic rollback. Inspect status before resuming; do not open both nodes.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
