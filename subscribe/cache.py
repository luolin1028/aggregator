# -*- coding: utf-8 -*-

"""Persistent incremental cache for the aggregator.

All state lives in a single JSON document on a persistent disk (e.g. the
InstaBox ``/data`` volume) so that consecutive runs can skip work:

* ``nodes``   - node fingerprint -> last verification result + timestamp
* ``domains`` - airport domains whose registration failed, to be skipped
* ``dns``     - host name -> resolved IPv4 + timestamp
* ``uploads`` - storage key -> content hash, to skip unchanged uploads
* ``history`` - group -> last verified proxy list, merged into the next run

The cache is best-effort: when the directory is not writable every method
silently degrades to a no-op so that normal runs are never broken.
"""

import hashlib
import json
import os
import tempfile
import threading
import time

from logger import logger

CACHE_DIR_ENV = "AGGREGATOR_CACHE_DIR"
CACHE_TTL_ENV = "AGGREGATOR_CACHE_TTL"
CACHE_DISABLE_ENV = "AGGREGATOR_CACHE_DISABLE"

# default TTL in seconds
NODE_TTL = 24 * 60 * 60
DNS_TTL = 6 * 60 * 60
DOMAIN_TTL = 7 * 24 * 60 * 60

# 历史节点列表上限，避免缓存文件无限增长
MAX_HISTORY = 3000

_REPO_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

# 节点指纹计算时忽略的字段（名称/来源/运行期标记不影响连通性）
_IGNORED_FIELDS = {"name", "sub", "liveness", "renamed"}

_VERSION = 1


def default_cache_dir() -> str:
    """Resolve the cache directory.

    Priority: ``AGGREGATOR_CACHE_DIR`` -> ``/data/aggregator-cache`` (when the
    persistent volume exists) -> ``<repo>/data/cache``.
    """
    configured = (os.environ.get(CACHE_DIR_ENV) or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))

    persistent = "/data"
    if os.path.isdir(persistent) and os.access(persistent, os.W_OK):
        return os.path.join(persistent, "aggregator-cache")

    return os.path.join(_REPO_ROOT, "data", "cache")


def node_fingerprint(proxy: dict) -> str:
    """Stable fingerprint built from protocol + address + port + key params."""
    if not isinstance(proxy, dict) or not proxy:
        return ""

    payload = {k: v for k, v in proxy.items() if k not in _IGNORED_FIELDS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def content_digest(content) -> str:
    """Deterministic digest for arbitrary cacheable content."""
    if isinstance(content, str):
        blob = content
    else:
        blob = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class PersistentCache:
    def __init__(self, directory: str = "", node_ttl: int = NODE_TTL) -> None:
        self.directory = directory or default_cache_dir()
        self.path = os.path.join(self.directory, "state.json")
        self.node_ttl = node_ttl
        self._lock = threading.RLock()
        self._data = None
        self._disabled = (os.environ.get(CACHE_DISABLE_ENV, "") or "").strip().lower() in ["true", "1"]

    # ------------------------------------------------------------------ io
    def _empty(self) -> dict:
        return {"version": _VERSION, "nodes": {}, "domains": {}, "dns": {}, "uploads": {}, "history": {}}

    def _load(self) -> dict:
        if self._data is not None:
            return self._data

        self._data = self._empty()
        if self._disabled:
            return self._data

        try:
            if os.path.exists(self.path) and os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    for key in ("nodes", "domains", "dns", "uploads", "history"):
                        value = loaded.get(key)
                        if isinstance(value, dict):
                            self._data[key] = value
        except Exception as exc:
            logger.warning(f"[Cache] cannot load cache from {self.path}: {exc}")
            self._data = self._empty()

        return self._data

    def flush(self) -> None:
        if self._disabled:
            return

        with self._lock:
            data = self._data or self._empty()
            try:
                os.makedirs(self.directory, exist_ok=True)
                directory = os.path.dirname(self.path) or "."
                fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=directory)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                    os.replace(tmp, self.path)
                except Exception:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    raise
            except Exception as exc:
                logger.warning(f"[Cache] cannot persist cache to {self.path}: {exc}")

    # --------------------------------------------------------------- nodes
    def get_node(self, fingerprint: str, ttl: int | None = None) -> bool | None:
        if not fingerprint:
            return None

        ttl = self.node_ttl if ttl is None else ttl
        with self._lock:
            record = self._load()["nodes"].get(fingerprint)
            if not isinstance(record, (list, tuple)) or len(record) < 2:
                return None
            alive, timestamp = bool(record[0]), record[1]
            try:
                timestamp = float(timestamp)
            except (TypeError, ValueError):
                return None
            if ttl > 0 and time.time() - timestamp > ttl:
                return None
            return alive

    def set_node(self, fingerprint: str, alive: bool, timestamp: float | None = None) -> None:
        if not fingerprint:
            return
        with self._lock:
            self._load()["nodes"][fingerprint] = [bool(alive), float(timestamp or time.time())]

    # ------------------------------------------------------------- domains
    def is_bad_domain(self, domain: str, ttl: int = DOMAIN_TTL) -> bool:
        domain = (domain or "").strip().lower()
        if not domain:
            return False
        with self._lock:
            timestamp = self._load()["domains"].get(domain)
            if timestamp is None:
                return False
            try:
                timestamp = float(timestamp)
            except (TypeError, ValueError):
                return False
            return ttl <= 0 or time.time() - timestamp <= ttl

    def mark_bad_domain(self, domain: str, timestamp: float | None = None) -> None:
        domain = (domain or "").strip().lower()
        if not domain:
            return
        with self._lock:
            self._load()["domains"][domain] = float(timestamp or time.time())

    # ----------------------------------------------------------------- dns
    def get_dns(self, host: str, ttl: int = DNS_TTL) -> str:
        host = (host or "").strip().lower()
        if not host:
            return ""
        with self._lock:
            record = self._load()["dns"].get(host)
            if not isinstance(record, (list, tuple)) or len(record) < 2:
                return ""
            ip, timestamp = record[0], record[1]
            try:
                timestamp = float(timestamp)
            except (TypeError, ValueError):
                return ""
            if ttl > 0 and time.time() - timestamp > ttl:
                return ""
            return str(ip or "")

    def set_dns(self, host: str, ip: str, timestamp: float | None = None) -> None:
        host, ip = (host or "").strip().lower(), (ip or "").strip()
        if not host or not ip:
            return
        with self._lock:
            self._load()["dns"][host] = [ip, float(timestamp or time.time())]

    # ------------------------------------------------------------- uploads
    def upload_unchanged(self, key: str, content) -> bool:
        key = (key or "").strip()
        if not key:
            return False
        digest = content_digest(content)
        with self._lock:
            record = self._load()["uploads"].get(key)
            if not isinstance(record, (list, tuple)) or not record:
                return False
            return record[0] == digest

    def remember_upload(self, key: str, content, timestamp: float | None = None) -> None:
        key = (key or "").strip()
        if not key:
            return
        digest = content_digest(content)
        with self._lock:
            self._load()["uploads"][key] = [digest, float(timestamp or time.time())]

    # ------------------------------------------------------------- history
    def get_history(self, group: str = "collect") -> list[dict]:
        key = (group or "collect").strip()
        with self._lock:
            items = self._load()["history"].get(key)
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if isinstance(item, dict) and item.get("type") and item.get("server")]

    def set_history(self, proxies: list[dict], group: str = "collect") -> None:
        key = (group or "collect").strip()
        cleaned = []
        for item in proxies or []:
            if not isinstance(item, dict) or not item.get("type") or not item.get("server"):
                continue
            cleaned.append({k: v for k, v in item.items() if k != "liveness"})
        with self._lock:
            self._load()["history"][key] = cleaned[-MAX_HISTORY:]

    # --------------------------------------------------------------- misc
    def purge(self, node_ttl: int | None = None, dns_ttl: int = DNS_TTL, domain_ttl: int = DOMAIN_TTL) -> None:
        node_ttl = self.node_ttl if node_ttl is None else node_ttl
        now = time.time()
        with self._lock:
            data = self._load()
            data["nodes"] = {
                k: v
                for k, v in data["nodes"].items()
                if isinstance(v, (list, tuple)) and len(v) >= 2 and now - float(v[1]) <= node_ttl
            }
            data["dns"] = {
                k: v
                for k, v in data["dns"].items()
                if isinstance(v, (list, tuple)) and len(v) >= 2 and now - float(v[1]) <= dns_ttl
            }
            data["domains"] = {
                k: v for k, v in data["domains"].items() if now - float(v) <= domain_ttl
            }


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def get_cache() -> PersistentCache:
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                node_ttl = NODE_TTL
                raw = (os.environ.get(CACHE_TTL_ENV) or "").strip()
                if raw:
                    try:
                        node_ttl = max(0, int(float(raw) * 3600))
                    except ValueError:
                        pass
                _INSTANCE = PersistentCache(node_ttl=node_ttl)
    return _INSTANCE
