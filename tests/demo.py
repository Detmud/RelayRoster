"""Starts RelayRoster with the simulated fleet, without real devices. For UI work and screenshots.

Usage: python tests/demo.py [port] [--slow-scan]   ->  http://127.0.0.1:8746

--slow-scan: SCAN_NETWORKS=192.168.0.0/16 plus an invalid entry, empty addresses only answer
after 150 ms. Then "Scan network" takes a good minute and progress, remaining time and cancelling
can be watched.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ["RELAYROSTER_NO_AUTOAPP"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app import discovery  # noqa: E402
from app.main import create_app  # noqa: E402
from fake_shelly import make_fleet  # noqa: E402
from test_core import make_settings  # noqa: E402


class SlowEmpty(httpx.AsyncBaseTransport):
    """Empty addresses answer with a delay, like an ARP timeout in a real network (only shorter)."""

    def __init__(self, fleet, delay: float):
        self.fleet, self.inner, self.delay = fleet, fleet.transport(), delay

    async def handle_async_request(self, request):
        if request.url.host not in self.fleet.devices:
            await asyncio.sleep(self.delay)
        return await self.inner.handle_async_request(request)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    slow = "--slow-scan" in sys.argv
    port = int(args[0]) if args else 8746
    tmp = tempfile.mkdtemp(prefix="relayroster-demo-")
    fleet = make_fleet(stacks=True)
    cfg = make_settings(tmp, shelly_password="secret-pw")
    app = create_app(cfg, transport=fleet.transport(), autostart=False)
    asyncio.run(app.state.collector.run(scan=True))
    if slow:
        cfg.scan_networks = ["192.168.0.0/16", "10.0.0.0/8"]
        app.state.collector.plan = discovery.plan_targets(cfg.scan_networks, [])
        app.state.collector.transport = SlowEmpty(fleet, 0.15)
    # Firmware: two stable updates and one beta offer, short check intervals for the demo
    fleet.devices["192.168.10.41"].stable = "1.7.6"
    fleet.devices["192.168.10.31"].beta = "2.2.0-beta1"
    app.state.collector.firmware.poll_s = 1.0
    app.state.collector.reboot_poll_s = 1.0   # restart: short check intervals for the demo
    # Peer comparison: cloud off, different servers -> does not count, listed under "hidden"
    for n, host in enumerate(("192.168.10.21", "192.168.10.22", "192.168.10.23")):
        fleet.devices[host].config["cloud"] = {"enable": False, "server": f"shelly-{n + 11}-eu.shelly.cloud:6022/jrpc"}
    # Add one more change, so the backup history shows something
    fleet.devices["192.168.10.31"].config["cover:0"]["power_limit"] = 250
    asyncio.run(app.state.collector.run())
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
