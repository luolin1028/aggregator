# -*- coding: utf-8 -*-

"""Two-stage proxy verification helpers.

Stage 1 (this module): resolve host names in bulk (with a persistent DNS
cache) and probe TCP reachability with a short timeout. Stage 2 (the caller)
runs the expensive protocol handshake / real request through mihomo.

Only nodes that pass the cheap TCP pre-check are worth the full check, and
results are cached so that recently verified nodes are not re-checked on the
next run.
"""

import ipaddress
import socket
import threading

import utils
from cache import get_cache, node_fingerprint
from logger import logger

DEFAULT_PROBE_TIMEOUT = 1.5
DEFAULT_BREAKER = 8


def _clean_host(host: str) -> str:
    host = utils.trim(str(host or ""))
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def is_ip(text: str) -> bool:
    host = _clean_host(text)
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _resolve_host(host: str) -> str:
    for family in (socket.AF_INET, socket.AF_UNSPEC):
        try:
            infos = socket.getaddrinfo(host, None, family=family, type=socket.SOCK_STREAM)
            for info in infos:
                ip = info[4][0] if len(info) > 4 and info[4] else ""
                if ip:
                    return ip
        except Exception:
            continue
    return ""


def resolve_hosts(hosts, num_threads: int = 64, display: bool = False) -> dict[str, str]:
    """Resolve host names concurrently, reusing the persistent DNS cache."""
    state = get_cache()
    wanted, mapping, missing = [], {}, []
    for raw in hosts or []:
        host = _clean_host(raw)
        key = host.lower()
        if not host or is_ip(host) or key in mapping or key in wanted:
            continue
        wanted.append(key)
        cached = state.get_dns(key)
        if cached:
            mapping[key] = cached
        else:
            missing.append(host)

    if missing:
        results = utils.multi_thread_run(
            func=_resolve_host,
            tasks=missing,
            num_threads=num_threads,
            show_progress=display,
            description="Resolving",
        )
        for index, host in enumerate(missing):
            ip = results[index] if index < len(results) else ""
            if not ip:
                continue
            mapping[host.lower()] = ip
            state.set_dns(host, ip)
        state.flush()

    return mapping


def tcp_connect(target: str, port, timeout: float = DEFAULT_PROBE_TIMEOUT) -> bool:
    target = _clean_host(target)
    if not target:
        return False
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if port <= 0 or port > 65535:
        return False

    try:
        with socket.create_connection((target, port), timeout=max(float(timeout), 0.2)):
            return True
    except OSError:
        return False


def precheck(
    proxies: list[dict],
    num_threads: int = 128,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    breaker: int = DEFAULT_BREAKER,
    display: bool = False,
) -> list[bool]:
    """TCP pre-check for every proxy, with a per-source failure circuit breaker."""
    if not proxies:
        return []

    servers = []
    for proxy in proxies:
        servers.append(_clean_host(proxy.get("server", "")) if isinstance(proxy, dict) else "")

    resolved = resolve_hosts(
        {server for server in servers if server and not is_ip(server)},
        num_threads=num_threads,
        display=False,
    )

    tasks = []
    for index, proxy in enumerate(proxies):
        server = servers[index]
        if not isinstance(proxy, dict) or not server:
            tasks.append((None, 0, ""))
            continue
        target = server if is_ip(server) else resolved.get(server.lower(), server)
        tasks.append((target, proxy.get("port", 0), utils.trim(str(proxy.get("sub", "")))))

    locker = threading.Lock()
    states: dict[str, dict] = {}

    def worker(target: str, port, source: str) -> bool:
        if breaker > 0 and source:
            with locker:
                record = states.get(source)
                if record and record.get("opened"):
                    return False

        alive = tcp_connect(target, port, timeout=timeout)

        if breaker > 0 and source:
            with locker:
                record = states.setdefault(source, {"fails": 0, "opened": False})
                if alive:
                    record["fails"] = 0
                else:
                    record["fails"] += 1
                    if record["fails"] >= breaker:
                        record["opened"] = True
        return alive

    return utils.multi_thread_run(
        func=worker,
        tasks=tasks,
        num_threads=num_threads,
        show_progress=display,
        description="TCP pre-check",
    )


def screen(
    proxies: list[dict],
    num_threads: int = 128,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    breaker: int = DEFAULT_BREAKER,
    display: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Split proxies using the cache and the TCP pre-check.

    Returns ``(cached_alive, candidates)`` where ``cached_alive`` are nodes
    verified alive within the TTL and ``candidates`` are TCP-reachable nodes
    that still need the full protocol check.
    """
    if not proxies:
        return [], []

    state = get_cache()
    cached_alive, dead, pending = [], 0, []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue
        status = state.get_node(node_fingerprint(proxy))
        if status is True:
            cached_alive.append(proxy)
        elif status is False:
            dead += 1
        else:
            pending.append(proxy)

    flags = precheck(pending, num_threads=num_threads, timeout=timeout, breaker=breaker, display=display)
    candidates = []
    for proxy, alive in zip(pending, flags):
        if alive:
            candidates.append(proxy)
        else:
            state.set_node(node_fingerprint(proxy), False)

    logger.info(
        f"[Probe] total: {len(proxies)}, cached alive: {len(cached_alive)}, "
        f"cached dead: {dead}, tcp pass: {len(candidates)}/{len(pending)}, tcp dead: {len(pending) - len(candidates)}"
    )
    state.flush()

    return cached_alive, candidates


def record(proxies: list[dict], results: list[bool]) -> None:
    """Persist the full-check result for nodes that reached stage 2."""
    state = get_cache()
    for proxy, alive in zip(proxies or [], results or []):
        if isinstance(proxy, dict):
            state.set_node(node_fingerprint(proxy), bool(alive))
