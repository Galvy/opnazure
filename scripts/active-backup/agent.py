#!/usr/local/bin/python3
"""Azure OPNsense active/backup: leased leadership, Compute fencing, HTTP probes.

No peer heartbeat election, no CARP and no storage keys. A candidate MUST observe
its peer powered off before advertising /health. Both LBs use this same endpoint.
The lease alone is NOT a fencing mechanism. See docs/active-backup.md.
"""
import argparse
import datetime
import fcntl
import http.server
import json
import logging
from pathlib import Path
import random
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LOG = logging.getLogger("opnazure-ha")
ROOT = Path("/conf/opnazure-ha")
LEASE_SECONDS = 60
SAFETY_SECONDS = 15


class Azure:
    def __init__(self, config):
        self.config = config
        self.tokens = {}
        # Do not forward IMDS or bearer tokens through environment proxy settings.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def token(self, resource):
        cached = self.tokens.get(resource)
        if cached and cached[1] > time.time() + 120:
            return cached[0]
        query = urllib.parse.urlencode({"api-version": "2018-02-01", "resource": resource,
                                       "client_id": self.config["client_id"]})
        request = urllib.request.Request(
            "http://169.254.169.254/metadata/identity/oauth2/token?" + query,
            headers={"Metadata": "true"})
        with self.http.open(request, timeout=3) as response:
            data = json.load(response)
        self.tokens[resource] = (data["access_token"], int(data["expires_on"]))
        return data["access_token"]

    def request(self, method, url, resource, headers=None):
        auth = {"Authorization": "Bearer " + self.token(resource)}
        auth.update(headers or {})
        request = urllib.request.Request(url, data=b"" if method in ("PUT", "POST") else None,
                                         headers=auth, method=method)
        with self.http.open(request, timeout=5) as response:
            return response.headers, response.read()

    def blob(self, method, query="", headers=None):
        common = {"x-ms-version": "2023-11-03",
                  "x-ms-date": datetime.datetime.now(datetime.timezone.utc).strftime(
                      "%a, %d %b %Y %H:%M:%S GMT")}
        common.update(headers or {})
        return self.request(method, self.config["blob_url"] + query,
                            "https://storage.azure.com/", common)

    def ensure_blob(self):
        try:
            self.blob("PUT", headers={"x-ms-blob-type": "BlockBlob", "If-None-Match": "*"})
        except urllib.error.HTTPError as error:
            # Existing blob, possibly leased: never overwrite it.
            if error.code not in (409, 412):
                raise
            self.blob("HEAD")  # Verify that it really exists and is accessible.

    def lease(self, action, lease_id):
        headers = {"x-ms-lease-action": action}
        if action == "acquire":
            headers.update({"x-ms-proposed-lease-id": lease_id,
                            "x-ms-lease-duration": str(LEASE_SECONDS)})
        else:
            headers["x-ms-lease-id"] = lease_id
        self.blob("PUT", "?comp=lease", headers)

    def owner(self):
        headers, _ = self.blob("HEAD")
        return headers.get("x-ms-meta-owner", "")

    def set_owner(self, owner, lease_id):
        self.blob("PUT", "?comp=metadata", {"x-ms-lease-id": lease_id,
                                          "x-ms-meta-owner": owner})

    def power(self, action):
        query = "?api-version=2024-11-01"
        if action == "powerOff":
            query += "&skipShutdown=true"
        return self.request("POST", "https://management.azure.com" +
                            self.config["peer_vm_id"] + "/" + action + query,
                            "https://management.azure.com/")

    def peer_state(self):
        _, body = self.request("GET", "https://management.azure.com" +
                               self.config["peer_vm_id"] +
                               "/instanceView?api-version=2024-11-01",
                               "https://management.azure.com/")
        states = [item["code"] for item in json.loads(body).get("statuses", [])
                  if item["code"].startswith("PowerState/")]
        return states[0] if states else "unknown"


class OpenVPN:
    def __init__(self, config, root=ROOT):
        self.config = config
        self.root = root

    def instances(self):
        # The operator adds UUIDs only after certificates/authentication are set up.
        values = json.loads((self.root / "instances.json").read_text())
        if not isinstance(values, list) or not values:
            raise ValueError("instances.json must contain at least one OpenVPN instance UUID")
        return [str(uuid.UUID(value)) for value in values]

    def armed(self):
        try:
            return (self.root / "armed").exists() and bool(self.instances())
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def maintenance(self):
        return (self.root / "maintenance").exists()

    def control(self, action):
        for instance in self.instances():
            subprocess.run(["/usr/local/sbin/configctl", "openvpn", action, instance],
                           check=True, timeout=20, stdout=subprocess.DEVNULL)

    def stopped(self):
        return all(not Path(f"/var/run/ovpn-instance-{item}.pid").exists() and
                   not Path(f"/var/etc/openvpn/instance-{item}.sock").exists()
                   for item in self.instances())

    def ready(self):
        try:
            for instance in self.instances():
                # Query the actual daemon, not the WebGUI or a PID alone.
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(f"/var/etc/openvpn/instance-{instance}.sock")
                    client.sendall(b"state\nquit\n")
                    output = bytearray()
                    while len(output) < 16384:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        output.extend(chunk)
                        if b",CONNECTED,SUCCESS," in output:
                            break
                    if b",CONNECTED,SUCCESS," not in output:
                        return False
            return True
        except (OSError, ValueError, TypeError):
            return False


class Controller:
    def __init__(self, azure, vpn, node, clock=time.monotonic):
        self.azure, self.vpn, self.node, self.clock = azure, vpn, node, clock
        self.lease_id = None
        self.deadline = 0
        self.phase = "standby"
        self.fence_requested = False
        self.fence_started = 0
        self.restart_peer = False
        self.ready_until = 0
        self.retry_at = 0
        self.initialized = False

    def valid(self):
        return self.lease_id is not None and self.clock() < self.deadline

    def healthy(self):
        return (self.phase == "active" and self.valid() and
                self.clock() < self.ready_until and self.vpn.armed() and
                not self.vpn.maintenance())

    def renew(self):
        if not self.valid():
            raise RuntimeError("lease expired locally; a new election and fencing are required")
        started = self.clock()
        self.azure.lease("renew", self.lease_id)
        self.deadline = started + LEASE_SECONDS - SAFETY_SECONDS
        if not self.valid():
            raise RuntimeError("lease response arrived too late")

    def demote(self, clean=False):
        # An in-flight Compute fencing/restart operation is not a clean handover.
        clean = clean and self.phase == "active" and not self.restart_peer
        self.phase = "standby"
        self.ready_until = 0
        # Stop OpenVPN before a voluntary release. If stop cannot be verified,
        # leave the owner record and lease intact: successor must hard-fence us.
        try:
            self.vpn.control("stop")
            stopped = self.vpn.stopped()
        except Exception:
            stopped = False
            LOG.exception("could not stop OpenVPN; successor must fence this VM")
        if self.valid() and clean and stopped:
            try:
                self.azure.set_owner("released", self.lease_id)
                self.azure.lease("release", self.lease_id)
            except Exception:
                LOG.exception("voluntary lease release failed")
        self.lease_id = None
        self.deadline = 0
        self.fence_requested = False
        self.restart_peer = False
        self.retry_at = self.clock() + 90  # let a healthy peer take over after a local failure

    def tick(self):
        if not self.vpn.armed() or self.vpn.maintenance():
            if self.lease_id or self.phase != "standby":
                self.demote(clean=True)
            # Also stop a daemon restarted by a GUI apply while in maintenance.
            elif self.vpn.armed() and self.vpn.ready():
                self.vpn.control("stop")
            return
        if self.clock() < self.retry_at:
            return
        if self.lease_id and not self.valid():
            self.demote()
            return
        if not self.lease_id:
            # Validate the warm standby before it can fence another VM.
            if not self.vpn.ready():
                self.vpn.control("start")
                if not self.vpn.ready():
                    raise RuntimeError("standby OpenVPN is not ready")
            if not self.initialized:
                self.azure.ensure_blob()
                self.initialized = True
            lease_id = str(uuid.uuid4())
            started = self.clock()
            try:
                self.azure.lease("acquire", lease_id)
            except urllib.error.HTTPError as error:
                if error.code == 409:
                    return
                raise
            self.lease_id = lease_id
            self.deadline = started + LEASE_SECONDS - SAFETY_SECONDS
            if not self.valid():
                raise RuntimeError("late lease acquisition")
            # A voluntary handover is recorded under the previous lease only
            # AFTER OpenVPN has stopped. Unknown/unclean owners require fencing.
            clean_handover = self.azure.owner() == "released"
            self.azure.set_owner(self.node, lease_id)
            self.phase = "starting" if clean_handover else "fencing"
            self.fence_started = self.clock()
            LOG.warning("lease acquired; %s", "clean handover" if clean_handover else "fencing peer before promotion")
        self.renew()
        if self.phase == "fencing":
            if self.clock() - self.fence_started > 240:
                raise RuntimeError("peer fencing timed out")
            if not self.fence_requested:
                self.azure.power("powerOff")
                self.fence_requested = True
                return
            state = self.azure.peer_state()
            if state not in ("PowerState/stopped", "PowerState/deallocated"):
                return
            self.phase = "starting"
            self.restart_peer = True
        if self.phase == "starting":
            self.renew()
            self.vpn.control("start")
            # Starting services can take time: revalidate ownership before any probe succeeds.
            self.renew()
            if not self.vpn.ready():
                raise RuntimeError("OpenVPN is not ready after start")
            self.phase = "active"
            LOG.warning("promoted to active after fencing or verified clean handover")
        if self.phase == "active":
            if not self.vpn.ready():
                raise RuntimeError("OpenVPN readiness failed")
            self.ready_until = self.clock() + 12
            if self.restart_peer:
                # Return the fenced VM to standby. Its agent starts with no lease.
                # Never use the peer's GUI/credentials for this action.
                self.renew()
                try:
                    self.azure.power("start")
                    self.restart_peer = False
                except Exception:
                    LOG.exception("active node healthy; peer restart will be retried")

    def run_once(self):
        try:
            self.tick()
        except Exception:
            LOG.exception("HA iteration failed; withdrawing health")
            if self.lease_id or self.phase == "active":
                self.demote()


def serve(controller, port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # Azure IPv4 health probe only. No management/control endpoints.
            if self.client_address[0] not in ("168.63.129.16", "127.0.0.1"):
                self.send_error(403)
                return
            healthy = self.path == "/health" and controller.healthy()
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"active\n" if healthy else b"unavailable\n")

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "azure.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # flock is released by the kernel on exit. A PID file alone is insufficient.
    lock = open(ROOT / "agent.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = json.loads(Path(args.config).read_text())
    controller = Controller(Azure(config), OpenVPN(config), config["node"])
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    server = serve(controller, config.get("probe_port", 8080))
    # Non-preemptive: primary gets an initial head start, never evicts a valid leader.
    stopped.wait(5 if config["node"] == "Primary" else 20)
    try:
        while not stopped.is_set():
            controller.run_once()
            stopped.wait(3 + random.random())
    finally:
        controller.demote(clean=True)
        server.shutdown()


if __name__ == "__main__":
    main()
