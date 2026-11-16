# Configuration

**Settings** in the app changes networks, intervals, timeouts, device password, snapshot count and the write switches
at runtime. Values are stored in the database under `/data` and apply immediately. Environment variables only provide
the **defaults**; each field shows its default and whether it was changed, "Reset to default" removes the change.

Only via the environment: `OIDC_*` (so a typo cannot lock you out), `DATA_DIR` and `LOG_LEVEL`.

| Variable | Default | Meaning |
|---|---|---|
| `SCAN_NETWORKS` | empty | Networks to scan (CIDR, comma-separated, at most /16 each). Nothing is scanned until you set it here or in Settings |
| `EXTRA_HOSTS` | empty | Additional single addresses |
| `POLL_INTERVAL_MINUTES` | `15` | Time between two full reads |
| `SCAN_EVERY_N_POLLS` | `4` | Network scan on every n-th read, `0` = only on demand |
| `SCAN_CONCURRENCY` | `128` | Parallel connections during a scan |
| `SCAN_TIMEOUT_SECONDS` | `1.5` | Timeout per address during a scan |
| `SHELLY_PASSWORD` | empty | Device password (digest auth, user `admin`) |
| `ALLOW_FIRMWARE_UPDATE` | `false` | Allow firmware updates |
| `FIRMWARE_PARALLEL` | `4` | Firmware updates running at the same time (1 to 16; 1 = one after the other) |
| `ALLOW_REBOOT` | `false` | Allow restarting devices |
| `ALLOW_ADDRESS` | `false` | Allow setting a static IP address from the address plan |
| `ALLOW_SET_PASSWORD` | `false` | Allow setting the device password on devices without one |
| `ALLOW_CONFIG` | `false` | Allow editing device configuration |
| `SIDEBAR_FOLDED` | `false` | Sidebar as an icon bar that expands on hover |
| `OIDC_ISSUER` | empty | Issuer URL of your identity provider; empty = no sign-in |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | empty | OIDC client; the secret can also be read from `OIDC_CLIENT_SECRET_FILE` |
| `OIDC_REDIRECT_URI` | empty | Fixed callback URL, e.g. `https://relayroster.example.com/auth/callback`; empty = derived from the request |
| `OIDC_ALLOWED_GROUPS` | empty | Comma-separated groups; empty = everyone the provider signs in for this client |
| `OIDC_CA_CERT` | empty | Extra root certificate (PEM) if your provider uses a private CA; also as `OIDC_CA_CERT_FILE` |
| `OIDC_SCOPES` | `openid profile email groups` | Requested scopes |
| `OIDC_PROVIDER_NAME` | `SSO` | Label of the "Sign in with …" button |
| `SNAPSHOTS_KEEP` | `100` | Snapshots kept per device |
| `DATA_DIR` | `/data` | SQLite database and `policy.yaml` |
| `LOG_LEVEL` | `INFO` | Log level |

The device password never leaves the server; the UI only shows whether it is set. It is stored in plain text in the
SQLite database under `/data`, so protect that volume.
