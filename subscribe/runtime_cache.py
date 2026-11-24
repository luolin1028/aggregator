"""Persistent cache helpers for runtime optimization."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any


def persistent_dir() -> str:
    candidates = ("/data", os.environ.get("LOCAL_DATA_DIR", ""), "")
    for candidate in candidates:
        if candidate:
            try:
                os.makedirs(candidate, exist_ok=True)
                return os.path.abspath(candidate)
            except (OSError, PermissionError):
                pass
    base = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
    os.makedirs(base, exist_ok=True)
    return base


class RuntimeCache:
    def __init__(self, filename: str = "runtime_cache.json") -> None:
        self.directory = persistent_dir()
        self.path = os.path.join(self.directory, filename)
        self.data: dict[str, Any] = {"nodes": {}, "domains": {}, "sources": {}, "uploads": {}}
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                for key in ("nodes", "domains", "sources", "uploads"):
                    value = payload.get(key)
                    if isinstance(value, dict):
                        self.data[key] = value
        except (OSError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        os.makedirs(self.directory, exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    @staticmethod
    def node_fingerprint(proxy: dict[str, Any]) -> str:
        important = (
            "type", "server", "port", "password", "uuid", "username", "network", "transport",
            "cipher", "alterId", "sni", "servername", "server_name", "host", "path", "tls",
            "security", "flow", "protocol", "plugin", "plugin_opts",
        )
        material = {key: proxy.get(key) for key in important if key in proxy}
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf8")).hexdigest()

    def node_result(self, fingerprint: str, max_age_hours: float) -> bool | None:
        record = self.data["nodes"].get(fingerprint)
        if not isinstance(record, dict):
            return None
        timestamp = float(record.get("timestamp", 0) or 0)
        if time.time() - timestamp > max_age_hours * 3600:
            return None
        value = record.get("alive")
        return bool(value) if isinstance(value, bool) else None

    def set_node_result(self, fingerprint: str, alive: bool) -> None:
        self.data["nodes"][fingerprint] = {"alive": alive, "timestamp": time.time()}

    def is_domain_blocked(self, domain: str) -> bool:
        return domain.lower() in {str(key).lower() for key in self.data["domains"]}

    def block_domain(self, domain: str, reason: str = "failed") -> None:
        if domain:
            self.data["domains"][domain.lower()] = {"reason": reason, "timestamp": time.time()}

    def upload_changed(self, name: str, content: str) -> bool:
        digest = hashlib.sha256(content.encode("utf8")).hexdigest()
        if self.data["uploads"].get(name) == digest:
            return False
        self.data["uploads"][name] = digest
        return True
