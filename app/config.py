"""Configuration via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    """Reads NAME or, if set, the content of the file from NAME_FILE (Docker secrets)."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        with open(file_path, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    val = _env(name)
    return int(val) if val not in (None, "") else default


def _float(name: str, default: float) -> float:
    val = _env(name)
    return float(val) if val not in (None, "") else default


def _bool(name: str, default: bool = False) -> bool:
    val = (_env(name) or "").strip().lower()
    return default if not val else val in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    raw = _env(name, default) or ""
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


@dataclass
class Settings:
    # Networks scanned for Shelly devices (CIDR, comma-separated). No default: every network is different
    scan_networks: list[str] = field(default_factory=lambda: _list("SCAN_NETWORKS", ""))
    # Additional fixed addresses, e.g. devices in another network
    extra_hosts: list[str] = field(default_factory=lambda: _list("EXTRA_HOSTS", ""))
    # Interval between two full reads. A network scan runs along on every n-th read,
    # 0 = never automatically, only via "Scan network".
    poll_interval_min: int = field(default_factory=lambda: _int("POLL_INTERVAL_MINUTES", 15))
    scan_every_n_polls: int = field(default_factory=lambda: _int("SCAN_EVERY_N_POLLS", 4))
    scan_concurrency: int = field(default_factory=lambda: _int("SCAN_CONCURRENCY", 128))
    scan_timeout_s: float = field(default_factory=lambda: _float("SCAN_TIMEOUT_SECONDS", 1.5))
    rpc_timeout_s: float = field(default_factory=lambda: _float("RPC_TIMEOUT_SECONDS", 6.0))
    # Device password (digest auth, user "admin"). Empty = devices without password protection.
    shelly_password: str = field(default_factory=lambda: _env("SHELLY_PASSWORD", "") or "")
    # Start firmware updates (Shelly.Update, channel stable/beta) from the UI. Off by default.
    allow_firmware_update: bool = field(default_factory=lambda: _bool("ALLOW_FIRMWARE_UPDATE", False))
    # Firmware updates running at the same time in one job. 1 = one device after the other.
    firmware_parallel: int = field(default_factory=lambda: _int("FIRMWARE_PARALLEL", 4))
    # Restart devices from the UI (Shelly.Reboot without parameters). Off by default.
    allow_reboot: bool = field(default_factory=lambda: _bool("ALLOW_REBOOT", False))
    # Set the device password (Settings) on devices without password (Shelly.SetAuth). Off by default.
    allow_set_password: bool = field(default_factory=lambda: _bool("ALLOW_SET_PASSWORD", False))
    # Edit configuration on the device page (<Component>.SetConfig, allow list app/writable.py)
    allow_config: bool = field(default_factory=lambda: _bool("ALLOW_CONFIG", False))
    # Set a static IP address from the address plan (WiFi.SetConfig / Eth.SetConfig, address fields only). Off by default.
    allow_address: bool = field(default_factory=lambda: _bool("ALLOW_ADDRESS", False))
    # Sidebar as an icon bar that expands on hover (Tabler "Folded hover"). Off: always expanded
    sidebar_folded: bool = field(default_factory=lambda: _bool("SIDEBAR_FOLDED", False))

    # Snapshots kept per device (the oldest are dropped)
    snapshots_keep: int = field(default_factory=lambda: _int("SNAPSHOTS_KEEP", 100))

    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", "/data") or "/data")
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO") or "INFO")

    # Sign-in via OIDC (the only sign-in), e.g. Pocket ID. Without OIDC_ISSUER no sign-in. Only via the environment, never in Settings.
    oidc_issuer: str = field(default_factory=lambda: (_env("OIDC_ISSUER", "") or "").rstrip("/"))
    oidc_client_id: str = field(default_factory=lambda: _env("OIDC_CLIENT_ID", "") or "")
    oidc_client_secret: str = field(default_factory=lambda: _env("OIDC_CLIENT_SECRET", "") or "")
    # Empty = every user the IdP signs in for this client. Otherwise at least one of these groups.
    oidc_allowed_groups: list[str] = field(default_factory=lambda: _list("OIDC_ALLOWED_GROUPS", ""))
    oidc_scopes: str = field(default_factory=lambda: _env("OIDC_SCOPES", "openid profile email groups")
                             or "openid profile email groups")
    # Name on the "Sign in with …" button
    oidc_provider_name: str = field(default_factory=lambda: _env("OIDC_PROVIDER_NAME", "SSO") or "SSO")
    # Fixed redirect URI, e.g. https://relayroster.example.com/auth/callback. Empty = derived from the request.
    oidc_redirect_uri: str = field(default_factory=lambda: _env("OIDC_REDIRECT_URI", "") or "")
    # Root certificate (PEM) that the connection to the IdP trusts in addition, e.g. your own internal CA.
    # As text in OIDC_CA_CERT or as a file via OIDC_CA_CERT_FILE.
    oidc_ca_cert: str = field(default_factory=lambda: _env("OIDC_CA_CERT", "") or "")

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "relayroster.sqlite3")

    @property
    def policy_path(self) -> str:
        return os.path.join(self.data_dir, "policy.yaml")


settings = Settings()
