"""Exercise planned handover, failure boundaries and operator recovery without Azure."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('manual', ROOT / 'scripts/manual-switchover.py')
manual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manual)

class FakeAzure:
    def __init__(self):
        self.states = {n: 'PowerState/running' for n in manual.NODES}
        self.rules = {n: {r: 'Allow' if n == 'Primary' else 'Deny' for r in manual.RULES}
                      for n in manual.NODES}
        self.events = []
        self.fail_stop = False
        self.fail_write = None
    def power(self, node):
        return self.states[node]
    def rule(self, node, rule):
        return self.rules[node][rule]
    def set_rule(self, node, rule, access):
        self.events.append(('rule', node, rule, access))
        if self.fail_write == (node, rule):
            raise RuntimeError('NSG update failed')
        self.rules[node][rule] = access
    def stop(self, node):
        self.events.append(('stop', node))
        if self.fail_stop:
            raise RuntimeError('Stop failed')
        self.states[node] = 'PowerState/deallocated'
    def start(self, node):
        self.events.append(('start', node))
        self.states[node] = 'PowerState/running'

class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.azure = FakeAzure()
    def test_target_is_enabled_only_after_old_node_stopped_and_isolated(self):
        manual.switch(self.azure, 'Secondary')
        e = self.azure.events
        stop = e.index(('stop', 'Primary'))
        closed = e.index(('rule', 'Primary', 'Workload-Outbound', 'Deny'))
        opened = e.index(('rule', 'Secondary', 'Workload-Outbound', 'Allow'))
        self.assertLess(stop, closed)
        self.assertLess(closed, opened)
        self.assertEqual(e[-1], ('rule', 'Secondary', 'Role-Probe', 'Allow'))
        self.assertEqual(self.azure.states['Primary'], 'PowerState/deallocated')
    def test_stop_failure_never_enables_target(self):
        self.azure.fail_stop = True
        with self.assertRaises(RuntimeError):
            manual.switch(self.azure, 'Secondary')
        self.assertTrue(all(v == 'Deny' for v in self.azure.rules['Secondary'].values()))
        self.assertFalse(any(e[1] == 'Secondary' for e in self.azure.events))
    def test_unconfirmed_stop_is_not_treated_as_success(self):
        self.azure.stop = lambda node: None
        with self.assertRaisesRegex(RuntimeError, 'not stopped'):
            manual.switch(self.azure, 'Secondary')
        self.assertEqual(self.azure.rules['Secondary']['Role-Probe'], 'Deny')
    def test_quarantine_failure_never_enables_target(self):
        self.azure.fail_write = ('Primary', 'Workload-Inbound')
        with self.assertRaises(RuntimeError):
            manual.switch(self.azure, 'Secondary')
        self.assertTrue(all(v == 'Deny' for v in self.azure.rules['Secondary'].values()))
    def test_partial_promotion_can_be_resumed_explicitly(self):
        self.azure.fail_write = ('Secondary', 'Workload-Inbound')
        with self.assertRaises(RuntimeError):
            manual.switch(self.azure, 'Secondary')
        self.assertEqual(self.azure.rules['Secondary']['Role-Probe'], 'Deny')
        self.assertEqual(self.azure.states['Primary'], 'PowerState/deallocated')
        self.azure.fail_write = None
        manual.switch(self.azure, 'Secondary')
        self.assertTrue(all(v == 'Allow' for v in self.azure.rules['Secondary'].values()))
    def test_stopped_target_is_rejected_without_changes(self):
        self.azure.states['Secondary'] = 'PowerState/deallocated'
        with self.assertRaisesRegex(RuntimeError, 'must be running'):
            manual.switch(self.azure, 'Secondary')
        self.assertEqual(self.azure.events, [])
    def test_repeat_selection_is_read_only(self):
        manual.switch(self.azure, 'Primary')
        self.assertEqual(self.azure.events, [])
    def test_old_node_restarts_only_while_isolated(self):
        manual.switch(self.azure, 'Secondary')
        manual.start_standby(self.azure, 'Primary')
        self.assertEqual(self.azure.events[-1], ('start', 'Primary'))
        self.assertTrue(all(v == 'Deny' for v in self.azure.rules['Primary'].values()))
    def test_start_selected_node_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Refusing startup'):
            manual.start_standby(self.azure, 'Primary')
        self.assertEqual(self.azure.events, [])
    def test_status_does_not_change_anything(self):
        state = manual.snapshot(self.azure)
        self.assertEqual(state['Secondary']['rules']['Role-Probe'], 'Deny')
        self.assertEqual(self.azure.events, [])
    def test_reverse_handover_uses_same_shutdown_order(self):
        manual.switch(self.azure, 'Secondary')
        manual.start_standby(self.azure, 'Primary')
        self.azure.events.clear()
        manual.switch(self.azure, 'Primary')
        self.assertLess(self.azure.events.index(('stop', 'Secondary')),
                        self.azure.events.index(('rule', 'Primary', 'Workload-Inbound', 'Allow')))

class CliTests(unittest.TestCase):
    def test_operator_cli_uses_explicit_subscription_without_shell(self):
        azure = manual.Azure('sub-id', 'test-rg', 'opn-vpn')
        result = subprocess.CompletedProcess([], 0, stdout='{}', stderr='')
        with patch.object(manual.subprocess, 'run', return_value=result) as run:
            azure.call('vm', 'deallocate', '--name', 'opn-vpn-Primary')
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], 'az')
        self.assertIn('sub-id', args[0])
        self.assertNotIn('--no-wait', args[0])
        self.assertNotIn('--force-deallocate', args[0])
        self.assertFalse(kwargs.get('shell', False))
    def test_changed_rule_shape_is_rejected(self):
        azure = manual.Azure('sub-id', 'test-rg', 'opn-vpn')
        azure.call = lambda *args: {'priority': 100, 'access': 'Allow', 'provisioningState': 'Succeeded'}
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            azure.rule('Primary', 'Role-Probe')

if __name__ == '__main__':
    unittest.main()
