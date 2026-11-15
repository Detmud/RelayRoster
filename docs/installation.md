# Installation

How to run RelayRoster with Docker, from a first try to permanent use.

You need Docker with Compose v2 and a host in the same network as your Shelly devices (the app talks to them via
HTTP on port 80). Images for amd64 and arm64 (e.g. Raspberry Pi 4/5) are published as
`ghcr.io/detmud/relayroster`. Two setups are included:

| Setup | Files | Sign-in | Use it for |
|---|---|---|---|
| **Try it** | `compose.yaml`, `.env.example` | none | a first look in your own network, a few minutes |
| **Run it permanently** | `examples/production/` | OIDC | everyday use behind a reverse proxy with HTTPS |

## Try it

```bash
mkdir relayroster && cd relayroster
curl -fsSLO https://raw.githubusercontent.com/Detmud/RelayRoster/main/compose.yaml
curl -fsSL  https://raw.githubusercontent.com/Detmud/RelayRoster/main/.env.example -o .env
nano .env                   # set SCAN_NETWORKS to the network your Shellies are in, e.g. 192.168.1.0/24
docker compose up -d
```

Open `http://<host>:8746`. The first scan of a /24 takes a few seconds; the menu next to "Sync now" in the header
starts another one. Networks, device password and everything else can also be changed later under **Settings**,
without a restart.

> [!WARNING]
> **This setup has no sign-in.** Anyone who can reach port 8746 can see your devices and use every write function
> you switch on in Settings: firmware updates, restarts, configuration changes, device passwords. That is fine for a
> first look in your own network. Before you keep it running, set up sign-in as described below. Never expose this
> setup to the internet.

Without Compose, the same in plain Docker:

```bash
docker run -d --name relayroster -p 8746:8080 \
  -e SCAN_NETWORKS=192.168.1.0/24 \
  -v relayroster-data:/data --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true \
  ghcr.io/detmud/relayroster:1
```

To stop and remove everything again, including all stored data: `docker compose down -v`.

## Run it permanently

`examples/production/` adds sign-in via OIDC, reads the client secret and the device password from files instead of
environment variables, and only listens on `127.0.0.1`, so the app is reachable through a reverse proxy on the same
host and nowhere else.

1. Create an OIDC client at your identity provider (see [Setting up OIDC](security.md#setting-up-oidc)): confidential client,
   PKCE on, callback URL `https://<your-host>/auth/callback`.
2. Configure and start:

   ```bash
   mkdir relayroster && cd relayroster
   curl -fsSLO https://raw.githubusercontent.com/Detmud/RelayRoster/main/examples/production/compose.yaml
   curl -fsSL  https://raw.githubusercontent.com/Detmud/RelayRoster/main/examples/production/.env.example -o .env
   nano .env                                  # fill in OIDC_* and SCAN_NETWORKS
   mkdir -p secrets
   printf '%s' '<client secret>' > secrets/oidc_client_secret
   printf '%s' '' > secrets/shelly_password   # or your device password
   sudo chown 10003 secrets/* && chmod 600 secrets/*
   docker compose up -d
   ```

   The container runs as UID 10003, so on Linux the secret files must belong to that user. With Docker Desktop or
   Colima on macOS the `chown` is not needed. Colima only shares your home directory with Docker by default, so keep
   the folder there.
3. Point your reverse proxy with HTTPS at `127.0.0.1:8746`. For Caddy that is

   ```
   relayroster.example.com {
       reverse_proxy 127.0.0.1:8746
   }
   ```

   If the proxy runs in Docker itself, put both containers into one Docker network and use `relayroster:8080`
   instead of publishing the port.
4. Open `https://relayroster.example.com`; it redirects to "Sign in with …". Problems with the sign-in (wrong
   callback URL, missing group, issuer mismatch) are shown on the sign-in page and in `docker logs relayroster`.

## Image tags

| Tag | Meaning |
|---|---|
| `1` | newest 1.x release, gets features and fixes, no breaking changes (used in the examples) |
| `1.2` | newest 1.2.x release, fixes only |
| `1.2.0` | exactly this release |
| `latest` | newest release of any major version |

## Updating and backups

```bash
docker compose pull && docker compose up -d    # in the folder of the setup you use
```

All data lives in the volume `relayroster-data` (SQLite database, settings, `policy.yaml`). Back up that volume, or
download all configuration backups as a ZIP from the Backups page. The device password is stored in plain text in the
database, so treat the volume like a secret.

## Building from source

```bash
git clone https://github.com/Detmud/RelayRoster.git && cd RelayRoster
cp .env.example .env
# in compose.yaml: comment out "image:" and enable "build: ."
docker compose up -d --build
```

## How discovery works

Discovery is an HTTP scan (`GET /shelly` on every address), because mDNS does not leave a Docker bridge network.
By default RelayRoster reads all devices every 15 minutes and rescans the networks every hour. Devices are identified
by their ID: if a device gets a new address, the next scan finds it there and its history stays. A device that
reports a new ID with the same MAC (e.g. after a firmware change) is taken over instead of listed twice.

## Large networks (up to /16)

Each network entry may be at most a /16. Scan time is driven almost entirely by **empty** addresses, since the scan
waits `SCAN_TIMEOUT_SECONDS` for each. Rule of thumb: addresses ÷ `SCAN_CONCURRENCY` × `SCAN_TIMEOUT_SECONDS`, so a /16
with the defaults takes 65,534 ÷ 128 × 1.5 s ≈ 13 minutes. The UI shows progress and remaining time; "Cancel" stops
the scan and keeps what was found. Every empty address costs an ARP broadcast, so for continuous operation limit
`SCAN_NETWORKS` to where your devices live, or set `SCAN_EVERY_N_POLLS=0`.
