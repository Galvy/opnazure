#!/usr/local/bin/python3
"""Active-backup controller. External NSG gates + finite lease + hard fencing.

Only UDP VPN services on non-control ports are supported. No peer power-on.
State is not synchronized VPN session state. See docs/active-backup.md.
"""
import argparse
import datetime
import fcntl
import http.server
import json
import logging
from pathlib import Path
import signal
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
SAFETY_SECONDS = 20
ARM = "https://management.azure.com"

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

    def request(self, method, url, resource, headers=None, body=None):
        auth = {"Authorization": "Bearer " + self.token(resource)}
        auth.update(headers or {})
        if body is not None:
            auth["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else (b"" if method in ("PUT", "POST") else None),
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

    def rule_url(self, nsg, direction):
        return ARM + nsg + "/securityRules/Workload-" + direction + "?api-version=2023-05-01"

    def gate_closed(self, nsg):
        for direction in ("Inbound", "Outbound"):
            _, raw = self.request("GET", self.rule_url(nsg, direction), ARM + "/")
            props = json.loads(raw)["properties"]
            if props.get("access") != "Deny" or props.get("provisioningState") != "Succeeded":
                return False
        return True

    def gate(self, nsg, opened, guard=lambda: None):
        # Guard before/after every write and poll. Completed NSG updates do not
        # terminate old flows: fencing is still mandatory on every takeover.
        directions = ("Outbound", "Inbound") if opened else ("Inbound", "Outbound")
        for direction in directions:
            guard()
            properties = dict(priority=200, direction=direction,
                              access="Allow" if opened else "Deny", protocol="*",
                              sourceAddressPrefix="*", sourcePortRange="*",
                              destinationAddressPrefix="*", destinationPortRange="*")
            url = self.rule_url(nsg, direction)
            self.request("PUT", url, ARM + "/", body={"properties": properties})
            until = time.monotonic() + 180
            while True:
                guard()
                _, raw = self.request("GET", url, ARM + "/")
                current = json.loads(raw)["properties"]
                state = current.get("provisioningState")
                if state == "Succeeded" and current.get("access") == properties["access"]:
                    break
                if state == "Failed" or time.monotonic() > until:
                    raise RuntimeError("NSG gate update failed or timed out")
                time.sleep(2)
            guard()

    def fence(self, guard):
        until = time.monotonic() + 240
        while True:
            guard()
            # A concurrent operator stop may still be in progress. There is no
            # automatic peer start in this controller.
            if self.peer_state() in ("PowerState/stopped", "PowerState/deallocated"):
                return
            try:
                headers, _ = self.power("powerOff")
                break
            except urllib.error.HTTPError as error:
                if error.code != 409 or time.monotonic() > until:
                    raise
                error.close()
                time.sleep(3)
        operation = headers.get("Azure-AsyncOperation") or headers.get("Location")
        if operation and not operation.startswith(ARM + "/"):
            raise RuntimeError("Unexpected Compute operation endpoint")
        while True:
            guard()
            complete = True
            if operation:
                _, body = self.request("GET", operation, ARM + "/")
                status = json.loads(body).get("status") if body else "Succeeded"
                if status in ("Failed", "Canceled"):
                    raise RuntimeError("Compute fencing failed")
                complete = status == "Succeeded"
            if complete and self.peer_state() in ("PowerState/stopped", "PowerState/deallocated"):
                return
            if time.monotonic() > until:
                raise RuntimeError("Compute fencing timed out")
            time.sleep(3)


class Controller:
    def __init__(self, azure, config, quiesce, clock=time.monotonic):
        self.azure, self.config, self.quiesce, self.clock = azure, config, quiesce, clock
        self.lease_id = None
        self.deadline = 0
        self.phase = "standby"
        self.initialized = False
        self.failed = False

    def valid(self):
        return bool(self.lease_id) and self.clock() < self.deadline and not self.failed

    def healthy(self):
        return self.phase == "active" and self.valid()

    def trip(self):
        self.phase = "isolated"
        self.failed = True
        # Do NOT release the lease: successor must wait and fence this VM.
        self.quiesce()

    def watchdog(self):
        if self.lease_id and not self.valid() and not self.failed:
            self.trip()

    def renew(self):
        if not self.valid():
            raise RuntimeError("No valid lease; fencing must be repeated")
        started = self.clock()
        self.azure.lease("renew", self.lease_id)
        if not self.valid():
            raise RuntimeError("Lease expired during renewal")
        self.deadline = started + LEASE_SECONDS - SAFETY_SECONDS
        if not self.valid():
            raise RuntimeError("Late renewal response")

    def tick(self):
        if self.failed:
            return
        if not self.initialized:
            self.azure.gate(self.config["own_nsg_id"], False)
            self.azure.ensure_blob()
            self.initialized = True
        if self.lease_id:
            self.renew()
            return
        lease = str(uuid.uuid4())
        started = self.clock()
        try:
            self.azure.lease("acquire", lease)
        except urllib.error.HTTPError as error:
            if error.code == 409:
                error.close()
                return
            raise
        self.deadline = started + LEASE_SECONDS - SAFETY_SECONDS
        self.lease_id = lease
        self.phase = "candidate"
        self.renew()
        previous = self.azure.owner()
        # Only a genuinely new witness can bootstrap without fencing. Both
        # initial NSGs are closed. Never delete/recreate the witness to fail over.
        fresh = not previous and self.azure.gate_closed(self.config["peer_nsg_id"])
        self.azure.set_owner(self.config["node"], lease)
        if not fresh:
            self.phase = "fencing"
            self.azure.fence(self.renew)
            self.azure.gate(self.config["peer_nsg_id"], False, self.renew)
        self.renew()
        self.azure.gate(self.config["own_nsg_id"], True, self.renew)
        self.renew()
        self.phase = "active"
        LOG.warning("ACTIVE: lease owned; prior owner fenced or first bootstrap verified")

    def run_once(self):
        try:
            self.tick()
        except Exception:
            LOG.exception("HA iteration failed")
            # Renewal may recover within the safety window. A partial promotion
            # cannot safely retry because a control-plane write may still finish.
            if self.lease_id and self.phase != "active":
                self.trip()
            self.watchdog()


def quiesce():
    # Fail closed locally even when the Azure control plane is unreachable.
    # Management is lost on this failed node too; Azure fencing remains possible.
    for nic in ("hn1", "hn0"):
        try:
            subprocess.run(["/sbin/ifconfig", nic, "down"], check=True, timeout=3)
        except Exception:
            LOG.exception("Could not disable %s; successor MUST fence", nic)


def serve(controller, port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.client_address[0] not in ("168.63.129.16", "127.0.0.1"):
                self.send_error(403)
                return
            self.send_response(200 if self.path == "/health" and controller.healthy() else 503)
            self.end_headers()
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
    lock = open(ROOT / "agent.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = json.loads(Path(args.config).read_text())
    ctl = Controller(Azure(config), config, quiesce)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    server = serve(ctl, config["probe_port"])
    def watch():
        while not stop.wait(1):
            ctl.watchdog()
    threading.Thread(target=watch, daemon=True).start()
    stop.wait(5 if config["node"] == "Primary" else 20)
    try:
        while not stop.is_set() and not ctl.failed:
            # First boot verification must complete before advertising HA health.
            if Path("/var/db/opnazure/first-boot-complete").exists():
                ctl.run_once()
            stop.wait(5)
    finally:
        if ctl.lease_id:
            ctl.trip()
        server.shutdown()

if __name__ == "__main__":
    main()
