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
| `NOTIFY_NTFY_URL` | empty | Full ntfy topic URL, e.g. `https://ntfy.sh/my-shelly-fleet`; empty = no ntfy messages |
| `NOTIFY_NTFY_TOKEN` | empty | Access token for a protected ntfy topic (sent as Bearer token) |
| `NOTIFY_WEBHOOK_URL` | empty | URL that receives a JSON POST per batch of events; empty = no webhook |
| `NOTIFY_OFFLINE` / `NOTIFY_NEW_DEVICE` / `NOTIFY_DRIFT` / `NOTIFY_WRITE_FAILED` | `true` | Switch single event types off |
| `NOTIFY_OFFLINE_AFTER` | `2` | Failed syncs in a row before a device counts as offline |
| `DATA_DIR` | `/data` | SQLite database and `policy.yaml` |
| `LOG_LEVEL` | `INFO` | Log level |

The device password never leaves the server; the UI only shows whether it is set. It is stored in plain text in the
SQLite database under `/data`, so protect that volume.

The ntfy access token is handled like the device password: never shown again, stored in the database.

## Notifications

RelayRoster can tell you about changes in the fleet through [ntfy](https://ntfy.sh) and/or a webhook (Home Assistant,
Node-RED, …). Set the URLs in **Settings → Notifications**, save, and use **Send test notification** to check them.
Without a URL nothing is sent. Notifications never write anything to a device.

| Event | When |
|---|---|
| `offline` | A device gave no answer for `NOTIFY_OFFLINE_AFTER` syncs in a row. A device that answers with an error (e.g. a missing password) counts as online |
| `online` | A device that was reported offline answers again. A single failed sync in between sends nothing |
| `new_device` | A scan found a device with an unknown ID and MAC. The very first inventory and devices that only got a new ID after a firmware update are not reported |
| `drift` | A finding (policy, firmware, naming plan, address plan, …) that the previous sync did not have. The first sync after start or after switching it on only records the current findings |
| `write_failed` | A failed entry in the activity log of a device (rename, settings, firmware, restart, …) |

Offline, online, new devices and drift are checked once at the end of every sync and sent as **one** message. Write
failures within 10 seconds are collected into one message, so a failing bulk change does not send one message per
device. The state is kept in the database, so a restart does not repeat messages.

**ntfy** gets the text as body, plus title, tags and priority (high for `offline` and `write_failed`). **The webhook**
gets JSON:

```json
{
  "source": "relayroster", "version": "1.1.0", "at": "2026-11-27T17:00:00+00:00", "test": false,
  "title": "RelayRoster: Offline",
  "text": "Offline:\n- Bedroom Wall Switch (shellyplus1-441793000301) at 192.168.10.41: no answer for 2 syncs (ConnectError: unreachable)",
  "events": [{"type": "offline", "device_id": "shellyplus1-441793000301", "device_name": "Bedroom Wall Switch",
              "host": "192.168.10.41", "failed_syncs": 2, "error": "ConnectError: unreachable"}]
}
```

Every event has `type`, `device_id`, `device_name` and `host`. `drift` adds `kind`, `source`, `path`, `label`,
`expected` and `actual`; `write_failed` adds `action` and `detail`; `new_device` adds `model`, `app` and `gen`.
Delivery has a timeout of 10 seconds and is not retried; the result of the last delivery is shown in Settings and
failures are logged. Topic and webhook URLs often work as secrets: anyone who can sign in to RelayRoster can see them.
