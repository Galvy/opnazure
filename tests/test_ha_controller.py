"""Fault injection for election ordering. No claim of Azure runtime validation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded

ha = module('ha', 'scripts/ha/agent.py')
installer = module('ha_install', 'scripts/ha/install.py')

class Clock:
    now = 0
    def __call__(self):
        return self.now

class Azure:
    def __init__(self, clock):
        self.clock = clock
        self.events = []
        self.previous = ''
        self.gates = {'own': False, 'peer': False}
        self.conflict = False
        self.fail_fence = False
        self.fail_renew = False
        self.fail_open = False
        self.late_acquire = False
        self.late_renew = False
    def ensure_blob(self):
        self.events.append('blob')
    def lease(self, action, lease):
        self.events.append(action)
        if action == 'acquire' and self.conflict:
            raise urllib.error.HTTPError('url', 409, 'leased', {}, None)
        if action == 'acquire' and self.late_acquire:
            self.clock.now += 61
        if action == 'renew' and self.fail_renew:
            raise OSError('unreachable')
        if action == 'renew' and self.late_renew:
            self.clock.now += 61
    def gate(self, nsg, opened, guard=lambda: None):
        guard()
        self.events.append(('gate', nsg, opened))
        if opened and self.fail_open:
            raise OSError('ambiguous update')
        self.gates[nsg] = opened
        guard()
    def gate_closed(self, nsg):
        return not self.gates[nsg]
    def owner(self):
        return self.previous
    def set_owner(self, owner, lease):
        self.previous = owner
        self.events.append('owner')
    def fence(self, guard):
        guard()
        self.events.append('fence')
        if self.fail_fence:
            raise OSError('Compute failed')
        self.events.append('stopped-confirmed')
        guard()

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.azure = Azure(self.clock)
        self.config = dict(node='Primary', own_nsg_id='own', peer_nsg_id='peer', probe_port=8080)
        self.quiesced = []
        self.ctl = ha.Controller(self.azure, self.config, lambda: self.quiesced.append(True), self.clock)

    def test_first_boot_keeps_peer_running_and_gated(self):
        self.ctl.tick()
        self.assertTrue(self.ctl.healthy())
        self.assertNotIn('fence', self.azure.events)
        self.assertFalse(self.azure.gates['peer'])
        self.assertLess(self.azure.events.index('owner'), self.azure.events.index(('gate', 'own', True)))

    def test_takeover_fences_and_quarantines_before_opening(self):
        self.azure.previous = 'Secondary'
        self.azure.gates['peer'] = True
        self.ctl.tick()
        events = self.azure.events
        self.assertLess(events.index('stopped-confirmed'), events.index(('gate', 'peer', False)))
        self.assertLess(events.index(('gate', 'peer', False)), events.index(('gate', 'own', True)))
        self.assertTrue(self.ctl.healthy())

    def test_unknown_initial_peer_gate_requires_fencing(self):
        self.azure.gates['peer'] = True
        self.ctl.tick()
        self.assertIn('fence', self.azure.events)

    def test_failed_fencing_never_opens_gate(self):
        self.azure.previous = 'Secondary'
        self.azure.fail_fence = True
        with self.assertLogs(ha.LOG, level='ERROR'):
            self.ctl.run_once()
        self.assertFalse(self.ctl.healthy())
        self.assertNotIn(('gate', 'own', True), self.azure.events)
        self.assertTrue(self.quiesced)

    def test_lease_conflict_is_standby_not_peer_failure(self):
        self.azure.conflict = True
        self.ctl.tick()
        self.ctl.tick()
        self.assertFalse(self.ctl.healthy())
        self.assertNotIn('fence', self.azure.events)
        self.assertEqual(self.azure.events.count(('gate', 'own', False)), 1)

    def test_renewal_outage_expires_and_locally_isolates(self):
        self.ctl.tick()
        self.azure.fail_renew = True
        with self.assertLogs(ha.LOG, level='ERROR'):
            self.ctl.run_once()
        self.assertTrue(self.ctl.healthy())  # still within the existing lease
        self.clock.now = 40
        self.ctl.watchdog()
        self.assertFalse(self.ctl.healthy())
        self.assertTrue(self.quiesced)
        self.assertNotIn('release', self.azure.events)

    def test_late_acquisition_never_opens(self):
        self.azure.late_acquire = True
        with self.assertLogs(ha.LOG, level='ERROR'):
            self.ctl.run_once()
        self.assertFalse(self.azure.gates['own'])
        self.assertTrue(self.quiesced)

    def test_late_renewal_cannot_resurrect_expired_authority(self):
        self.ctl.tick()
        self.azure.late_renew = True
        with self.assertLogs(ha.LOG, level='ERROR'):
            self.ctl.run_once()
        self.assertFalse(self.ctl.healthy())
        self.assertTrue(self.quiesced)

    def test_partial_gate_update_isolates_instead_of_retrying(self):
        self.azure.fail_open = True
        with self.assertLogs(ha.LOG, level='ERROR'):
            self.ctl.run_once()
        count = len(self.azure.events)
        self.ctl.tick()
        self.assertEqual(count, len(self.azure.events))
        self.assertTrue(self.quiesced)

    def test_recovered_primary_never_preempts_valid_owner(self):
        self.azure.previous = 'Secondary'
        self.azure.conflict = True
        for _ in range(5):
            self.ctl.tick()
        self.assertNotIn('fence', self.azure.events)

    def test_installer_preserves_manual_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            config = prefix / 'conf/config.xml'
            config.parent.mkdir()
            config.write_text('<manual-vpn/>')
            installer.install({'ha': self.config}, ROOT / 'scripts/ha/agent.py', prefix)
            self.assertEqual(config.read_text(), '<manual-vpn/>')
            self.assertEqual((prefix / 'conf/opnazure-ha/azure.json').stat().st_mode & 0o777, 0o600)
            hook = prefix / 'usr/local/etc/rc.syshook.d/early/01-opnazure-ha'
            self.assertIn('rm -f /var/db/opnazure/first-boot-complete', hook.read_text())

class RestTests(unittest.TestCase):
    def test_poweroff_uses_hard_fence_and_waits_for_operation(self):
        azure = ha.Azure({'peer_vm_id': '/subscriptions/s/resourceGroups/r/providers/Microsoft.Compute/virtualMachines/v'})
        calls = []
        replies = [({}, b'{"statuses":[{"code":"PowerState/running"}]}'),
                   ({'Azure-AsyncOperation': ha.ARM + '/operations/1'}, b''),
                   ({}, b'{"status":"InProgress"}'), ({}, b'{"status":"Succeeded"}'),
                   ({}, b'{"statuses":[{"code":"PowerState/stopped"}]}')]
        def request(*args, **kwargs):
            calls.append(args)
            return replies.pop(0)
        azure.request = request
        with patch.object(ha.time, 'sleep'):
            azure.fence(lambda: None)
        self.assertIn('skipShutdown=true', calls[1][1])
        self.assertEqual(calls[2][1], ha.ARM + '/operations/1')
        self.assertIn('/instanceView?', calls[-1][1])

    def test_no_token_is_sent_to_untrusted_operation_endpoint(self):
        azure = ha.Azure({})
        azure.peer_state = lambda: 'PowerState/running'
        azure.power = lambda action: ({'Location': 'https://attacker.invalid/op'}, b'')
        with self.assertRaisesRegex(RuntimeError, 'endpoint'):
            azure.fence(lambda: None)

    def test_already_deallocated_peer_is_not_sent_invalid_poweroff(self):
        azure = ha.Azure({})
        azure.peer_state = lambda: 'PowerState/deallocated'
        azure.power = lambda action: self.fail('must not powerOff a deallocated VM')
        azure.fence(lambda: None)

    def test_concurrent_operator_stop_conflict_is_rechecked(self):
        azure = ha.Azure({})
        states = iter(['PowerState/stopping', 'PowerState/stopped'])
        azure.peer_state = lambda: next(states)
        def conflict(action):
            raise urllib.error.HTTPError('url', 409, 'busy', {}, None)
        azure.power = conflict
        with patch.object(ha.time, 'sleep'):
            azure.fence(lambda: None)

    def test_closed_gate_requires_both_updates_completed(self):
        azure = ha.Azure({})
        replies = [({}, b'{"properties":{"access":"Deny","provisioningState":"Succeeded"}}'),
                   ({}, b'{"properties":{"access":"Deny","provisioningState":"Updating"}}')]
        azure.request = lambda *a: replies.pop(0)
        self.assertFalse(azure.gate_closed('/nsg'))

if __name__ == '__main__':
    unittest.main()
