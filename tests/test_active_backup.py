import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/active-backup' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

agent = module('agent')
configure = module('configure')

class Clock:
    now = 100
    def __call__(self):
        return self.now

class Azure:
    def __init__(self, clock):
        self.clock = clock
        self.held = None
        self.until = 0
        self.owner_name = ''
        self.state = 'PowerState/running'
        self.actions = []
        self.fail_renew = False
        self.fail_fence = False
        self.fail_restart = False
        self.slow_acquire = False

    def ensure_blob(self): pass
    def owner(self): return self.owner_name
    def set_owner(self, value, lease_id):
        if lease_id != self.held or self.clock() >= self.until:
            raise RuntimeError('lost lease')
        self.owner_name = value

    def lease(self, action, lease_id):
        if action == 'acquire':
            if self.held and self.clock() < self.until:
                raise urllib.error.HTTPError('test', 409, 'held', {}, io.BytesIO())
            self.held = lease_id
        elif action == 'release':
            if lease_id != self.held: raise RuntimeError('not owner')
            self.held = None
            return
        elif self.fail_renew or lease_id != self.held:
            raise RuntimeError('renew failed')
        self.until = self.clock() + 60
        if action == 'acquire' and self.slow_acquire:
            self.clock.now += 50

    def power(self, action):
        self.actions.append(action)
        if action == 'powerOff' and self.fail_fence: raise RuntimeError('403')
        if action == 'start' and self.fail_restart: raise RuntimeError('restart denied')
    def peer_state(self): return self.state

class VPN:
    def __init__(self):
        self.is_armed, self.is_maintenance, self.is_ready = True, False, True
        self.stop_fails = False
        self.start_fails = False
        self.actions = []
    def armed(self): return self.is_armed
    def maintenance(self): return self.is_maintenance
    def ready(self): return self.is_ready
    def stopped(self): return not self.is_ready
    def control(self, action):
        self.actions.append(action)
        if action == 'stop':
            if self.stop_fails: raise RuntimeError('stop failed')
            self.is_ready = False
        if action == 'start': self.is_ready = not self.start_fails

class ElectionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.azure = Azure(self.clock)
        self.vpn = VPN()
        self.c = agent.Controller(self.azure, self.vpn, 'Primary', self.clock)

    def activate(self):
        self.c.run_once()
        self.azure.state = 'PowerState/stopped'
        self.c.run_once()
        self.assertTrue(self.c.healthy())

    def test_initial_probe_is_closed(self):
        self.assertFalse(self.c.healthy())

    def test_never_promotes_before_fencing_confirmed(self):
        self.c.run_once()
        self.assertEqual(self.azure.actions, ['powerOff'])
        for _ in range(5): self.c.run_once()
        self.assertFalse(self.c.healthy())
        self.assertEqual(self.c.phase, 'fencing')
        self.azure.state = 'PowerState/stopped'
        self.c.run_once()
        self.assertTrue(self.c.healthy())
        self.assertEqual(self.azure.actions, ['powerOff', 'start'])

    def test_unknown_power_state_never_promotes(self):
        self.c.run_once()
        self.azure.state = 'unknown'
        self.c.run_once()
        self.assertFalse(self.c.healthy())

    def test_deallocated_peer_is_safely_fenced(self):
        self.c.run_once()
        self.azure.state = 'PowerState/deallocated'
        self.c.run_once()
        self.assertTrue(self.c.healthy())

    def test_fence_permission_failure_never_promotes(self):
        self.azure.fail_fence = True
        self.c.run_once()
        self.assertFalse(self.c.healthy())
        self.assertIsNone(self.c.lease_id)
        self.assertNotEqual(self.azure.owner_name, 'released')

    def test_no_preemption_of_existing_leader(self):
        self.azure.lease('acquire', 'other')
        self.c.run_once()
        self.assertFalse(self.c.healthy())
        self.assertEqual(self.azure.actions, [])

    def test_expired_lease_forces_new_election_not_renewal(self):
        self.activate()
        self.clock.now += 46
        self.assertFalse(self.c.healthy())
        self.c.run_once()
        self.assertIsNone(self.c.lease_id)
        self.assertFalse(self.vpn.ready())

    def test_stale_health_fails_even_with_lease_remaining(self):
        self.activate()
        self.clock.now += 13
        self.assertFalse(self.c.healthy())
        self.assertTrue(self.c.valid())

    def test_renew_failure_withdraws_and_stops_vpn(self):
        self.activate()
        self.azure.fail_renew = True
        self.c.run_once()
        self.assertFalse(self.c.healthy())
        self.assertFalse(self.vpn.ready())
        self.assertEqual(self.azure.owner_name, 'Primary')

    def test_unhealthy_vpn_relinquishes_and_cools_down(self):
        self.activate()
        self.vpn.is_ready = False
        self.c.run_once()
        self.assertFalse(self.c.healthy())
        self.assertGreater(self.c.retry_at, self.clock())

    def test_unhealthy_standby_cannot_fence(self):
        self.vpn.is_ready = False
        self.vpn.start_fails = True
        self.c.run_once()
        self.assertEqual(self.azure.actions, [])
        self.assertIsNone(self.c.lease_id)

    def test_maintenance_releases_only_after_verified_stop(self):
        self.activate()
        self.vpn.is_maintenance = True
        self.assertFalse(self.c.healthy())
        self.c.run_once()
        self.assertIsNone(self.azure.held)
        self.assertEqual(self.azure.owner_name, 'released')
        self.assertFalse(self.vpn.ready())

    def test_failed_stop_does_not_write_clean_handover(self):
        self.activate()
        self.vpn.stop_fails = True
        self.vpn.is_maintenance = True
        self.c.run_once()
        self.assertIsNotNone(self.azure.held)
        self.assertEqual(self.azure.owner_name, 'Primary')

    def test_clean_handover_does_not_poweroff_node_being_upgraded(self):
        self.azure.owner_name = 'released'
        self.c.run_once()
        self.assertTrue(self.c.healthy())
        self.assertEqual(self.azure.actions, [])

    def test_restart_failure_does_not_drop_healthy_active(self):
        self.azure.fail_restart = True
        self.activate()
        self.assertTrue(self.c.restart_peer)
        self.azure.fail_restart = False
        self.c.run_once()
        self.assertFalse(self.c.restart_peer)

    def test_disarmed_deployment_never_fences(self):
        self.vpn.is_armed = False
        self.c.run_once()
        self.assertEqual(self.azure.actions, [])
        self.assertFalse(self.c.healthy())

    def test_late_acquire_never_fences(self):
        self.azure.slow_acquire = True
        self.c.run_once()
        self.assertEqual(self.azure.actions, [])
        self.assertFalse(self.c.healthy())

    def test_fencing_timeout_never_promotes(self):
        self.c.run_once()
        for _ in range(81):
            self.clock.now += 3
            self.c.run_once()
        self.assertFalse(self.c.healthy())
        self.assertIsNone(self.c.lease_id)

    def test_shutdown_during_fencing_cannot_skip_fence_on_next_election(self):
        self.c.run_once()
        self.c.demote(clean=True)
        self.assertNotEqual(self.azure.owner_name, 'released')

class ConfigurationTests(unittest.TestCase):
    def test_uuid_input_rejects_shell_or_path_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'armed').touch()
            (root/'instances.json').write_text('["../../bad; reboot"]')
            self.assertFalse(agent.OpenVPN({}, root).armed())

    def test_bootstrap_rules_are_idempotent_and_probe_is_restricted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.xml'
            path.write_bytes((ROOT/'scripts/config-active-active-secondary.xml').read_bytes())
            settings = {'node':'Primary', 'probe_port':8080, 'openvpn_port':1194}
            configure.configure(path, settings)
            configure.configure(path, settings)
            root = ET.parse(path).getroot()
            rules = [r for r in root.findall('filter/rule') if (r.findtext('descr') or '').startswith('OPNazure HA:')]
            self.assertEqual(len(rules), 3)
            probes = [r for r in rules if r.findtext('protocol') == 'tcp']
            self.assertTrue(all(r.findtext('source/address') == '168.63.129.16' for r in probes))
            self.assertIsNone(root.find('hasync'))


class FailoverTests(unittest.TestCase):
    def test_two_nodes_sudden_loss_then_stale_leader_resumes(self):
        clock = Clock()
        shared = Azure(clock)
        a = agent.Controller(shared, VPN(), 'Primary', clock)
        b = agent.Controller(shared, VPN(), 'Secondary', clock)
        a.run_once()
        shared.state = 'PowerState/stopped'
        a.run_once()
        b.run_once()
        self.assertTrue(a.healthy())
        self.assertFalse(b.healthy())
        # A is abruptly unavailable (no demotion or release).
        clock.now += 61
        shared.state = 'PowerState/running'
        b.run_once()
        self.assertFalse(a.healthy())
        self.assertFalse(b.healthy())  # B must first hard-fence A.
        shared.state = 'PowerState/stopped'
        b.run_once()
        self.assertTrue(b.healthy())
        # A stale process resuming cannot renew a lease it lost.
        a.run_once()
        self.assertFalse(a.healthy())
        self.assertTrue(b.healthy())

    def test_clean_two_node_handover_does_not_reboot_maintained_node(self):
        clock = Clock()
        shared = Azure(clock)
        avpn = VPN()
        a = agent.Controller(shared, avpn, 'Primary', clock)
        b = agent.Controller(shared, VPN(), 'Secondary', clock)
        a.run_once()
        shared.state = 'PowerState/stopped'
        a.run_once()
        shared.actions.clear()
        avpn.is_maintenance = True
        a.run_once()
        b.run_once()
        self.assertFalse(a.healthy())
        self.assertTrue(b.healthy())
        self.assertEqual(shared.actions, [])

class AzureTransportTests(unittest.TestCase):
    def test_blob_creation_never_overwrites_existing_witness(self):
        client = agent.Azure({})
        with patch.object(client, 'blob') as blob:
            blob.side_effect = [urllib.error.HTTPError('test', 412, '', {}, io.BytesIO()), ({}, b'')]
            client.ensure_blob()
            self.assertEqual(blob.call_args_list[0].kwargs['headers']['If-None-Match'], '*')
            self.assertEqual(blob.call_args_list[1].args[0], 'HEAD')

    def test_storage_auth_failure_is_not_misread_as_existing_blob(self):
        client = agent.Azure({})
        with patch.object(client, 'blob') as blob:
            blob.side_effect = urllib.error.HTTPError('test', 403, '', {}, io.BytesIO())
            with self.assertRaises(urllib.error.HTTPError): client.ensure_blob()

    def test_poweroff_is_hard_and_scoped_to_peer(self):
        peer = '/subscriptions/s/resourceGroups/r/providers/Microsoft.Compute/virtualMachines/peer'
        client = agent.Azure({'peer_vm_id': peer})
        with patch.object(client, 'request') as request:
            client.power('powerOff')
            method, url, audience = request.call_args.args
            self.assertEqual(method, 'POST')
            self.assertIn(peer+'/powerOff?', url)
            self.assertIn('skipShutdown=true', url)
            self.assertEqual(audience, 'https://management.azure.com/')

class ProbeTests(unittest.TestCase):
    def test_http_probe_withdraws_on_maintenance_without_waiting_for_tick(self):
        import urllib.request
        clock = Clock()
        azure, vpn = Azure(clock), VPN()
        c = agent.Controller(azure, vpn, 'Primary', clock)
        c.run_once()
        azure.state = 'PowerState/stopped'
        c.run_once()
        server = agent.serve(c, 0)
        url = 'http://127.0.0.1:%d/health' % server.server_address[1]
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                self.assertEqual(response.status, 200)
            vpn.is_maintenance = True
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url, timeout=2)
            self.assertEqual(error.exception.code, 503)
        finally:
            server.shutdown()
            server.server_close()

if __name__ == '__main__':
    unittest.main()
