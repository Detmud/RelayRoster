"""Finds Shelly Gen2+ devices via HTTP scan.

mDNS does not leave a Docker bridge network, so the scan asks every address of the
configured networks for `GET /shelly` (reachable without auth). Gen1 devices answer there without a
`gen` field and are ignored.

Networks up to /16 (65,534 addresses) are allowed. The addresses come from a generator, a fixed
number of workers processes them: memory use and the number of parallel connections do not depend on the
network size.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Iterator

import httpx

log = logging.getLogger(__name__)

MAX_PREFIX = 16                     # largest network per entry in SCAN_NETWORKS
MAX_ADDRESSES = 2 ** (32 - MAX_PREFIX)

ProgressFn = Callable[[int, int, int], None]                       # (checked, total, found)
FoundFn = Callable[[str, dict[str, Any]], Awaitable[None] | None]  # (host, /shelly-Antwort)


@dataclass
class TargetPlan:
    """What a scan will check: merged networks, single addresses, count, warnings."""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(_host_count(n) for n in self.networks) + len(self.hosts)

    def __iter__(self) -> Iterator[str]:
        for net in self.networks:
            for ip in net.hosts():
                yield str(ip)
        yield from self.hosts


def _host_count(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> int:
    # hosts() omits network and broadcast address, except for /31 and /32
    n = net.num_addresses
    return n if n <= 2 else n - 2


def plan_targets(networks: Iterable[str], extra_hosts: Iterable[str] = ()) -> TargetPlan:
    """Checks the configuration, merges overlapping networks and removes duplicate addresses."""
    plan = TargetPlan()
    accepted: list[ipaddress.IPv4Network] = []
    for raw in networks:
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            plan.warnings.append(f"'{raw}' is not a valid network (example: 192.168.10.0/24) and is ignored.")
            continue
        if net.version != 4:
            plan.warnings.append(f"{net} is IPv6. The scan supports IPv4 networks only, entry ignored.")
            continue
        if net.num_addresses > MAX_ADDRESSES:
            count = f"{net.num_addresses:,}"
            plan.warnings.append(
                f"{net} is too large ({count} addresses). The maximum is /{MAX_PREFIX}, entry ignored.")
            continue
        accepted.append(net)
    plan.networks = list(ipaddress.collapse_addresses(accepted))

    seen: set[str] = set()
    for raw in extra_hosts:
        h = (raw or "").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            plan.hosts.append(h)          # host name, queried as is
            continue
        if any(ip in n for n in plan.networks):
            continue                       # already inside a scanned network
        plan.hosts.append(h)
    for w in plan.warnings:
        log.warning("SCAN_NETWORKS: %s", w)
    return plan


def expand_targets(networks: Iterable[str], extra_hosts: Iterable[str]) -> list[str]:
    """All addresses as a list. Only for small networks and tests, the scan itself iterates."""
    return list(plan_targets(networks, extra_hosts))


async def probe(http: httpx.AsyncClient, host: str, timeout: float) -> dict[str, Any] | None:
    try:
        resp = await http.get(f"http://{host}/shelly", timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    gen = data.get("gen")
    if not isinstance(gen, int) or gen < 2 or not data.get("id"):
        return None
    return data


async def scan(
    http: httpx.AsyncClient,
    networks: Iterable[str] | TargetPlan,
    extra_hosts: Iterable[str] = (),
    concurrency: int = 128,
    timeout: float = 1.5,
    progress: ProgressFn | None = None,
    on_found: FoundFn | None = None,
    cancel: asyncio.Event | None = None,
) -> dict[str, dict[str, Any]]:
    """Returns {host: /shelly answer} for all Gen2+ devices found.

    progress is called after every checked address, on_found immediately for every find.
    If cancel is set, the workers take no new addresses; what was found until then stays.
    """
    plan = networks if isinstance(networks, TargetPlan) else plan_targets(networks, extra_hosts)
    total = plan.total
    targets = iter(plan)
    found: dict[str, dict[str, Any]] = {}
    done = 0

    async def worker() -> None:
        nonlocal done
        # One event loop, one thread: next() on the shared generator is safe here without a lock
        for host in targets:
            if cancel is not None and cancel.is_set():
                return
            info = await probe(http, host, timeout)
            done += 1
            if info:
                found[host] = info
                if on_found is not None:
                    res = on_found(host, info)
                    if asyncio.iscoroutine(res):
                        await res
            if progress is not None:
                progress(done, total, len(found))

    workers = max(1, min(concurrency, total or 1))
    if progress is not None:
        progress(0, total, 0)
    await asyncio.gather(*(worker() for _ in range(workers)))
    stopped = " (abgebrochen)" if cancel is not None and cancel.is_set() else ""
    log.info("Scan%s: %d of %d addresses checked, %d devices found", stopped, done, total, len(found))
    return found
