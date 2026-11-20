# Target policy

On first start RelayRoster writes a commented template (`app/default_policy.yaml`). Paths follow `Shelly.GetConfig`,
levels separated by dots, wildcards per level:

```yaml
baseline:                                   # applies to all devices
  sys.location.tz: Europe/Berlin
  wifi.ap.enable: false
  switch:*.power_limit: {max: 3000}         # operators: in, not, min, max

profiles:                                   # per device function
  - name: Shutter
    match: {profile: cover}                 # fields: kind, variant, app, model, profile, name, id, gen (globs, list = or)
    settings:
      cover:*.obstruction_detection.enable: true
    ignore: [wifi.ap.enable]                # exempt a baseline rule for these devices

overrides:                                  # single devices
  shelly2pmg4-1020ba000201:
    settings:
      cover:0.power_limit: 250              # replaces the broader rule cover:*.power_limit
```

Order: baseline, then every matching profile in file order, then the device override; a later level wins per
concrete path. Rules for components a device does not have (cover on a Plus 1) are skipped. If the component exists
but the setting does not, the finding is "missing" (usually older firmware).

| Layer | Use it for | Example |
|---|---|---|
| `baseline` | every device | time zone, access point off |
| `profiles` | per **function**, not per room; `match` on `kind`/`profile` | "Shutter", "Button input" |
| `overrides` | only device-specific, measured values, **with a comment saying why and when** | `cover:0.power_limit` per motor |
| `peer_ignore` | values that must differ per device | names, MQTT client ID |

- `devices: [<id>, …]` adds devices to a profile in addition to `match`, `exclude: [<id>, …]` removes them.
- Use limits like `{max: …}` or `{not: …}` so factory defaults stand out; measured values belong in `overrides`.
- The template contains two example profiles, **Shutter** (obstruction detection, power limit, state after power
  loss, calibration check) and **Button input** (Shelly i4 as pure push-button input). An existing
  `/data/policy.yaml` is never overwritten.

The **Policy** page edits the baseline and the profiles without YAML (tab Rules) and lists the overrides; the tab
YAML edits the whole file. Both write round-trip (ruamel.yaml): comments, order and formatting are kept. Before
every save the previous file is kept as `policy.yaml.bak-<time>` (the last 20 remain).

## Address plan (`network`)

```yaml
network:
  subnet: 192.168.50.0/24         # where all devices should be
  dhcp_pool: 192.168.1.0/24       # where your router puts new devices
  gateway: 192.168.50.1           # only for "Apply address plan": used for devices that are on DHCP
  nameserver: 192.168.50.1
  blocks:                         # address block per type, the first match wins
    - {name: Shelly 1, match: {kind: "1"}, range: 192.168.50.10-19}
    - {name: Shelly i4, match: {kind: i4}, range: 192.168.50.30-39}
```

Findings (filter "Address") and a badge next to the IP report devices in the DHCP pool, outside the subnet, in the
wrong block or in no block, and suggest the next free address in the block. With "Allow address changes" on,
"Apply address plan" on the Findings page sets that address as a static address on the device, see
[Write functions](write-functions.md).

## Naming plan (`names`)

```yaml
names:
  shellyplusi4-b8d61a000102: Kitchen Wall Switch
  shelly2pmg4-58e6c5000202: Bedroom Shutter
```

A device whose `sys.device.name` differs from the plan gets a "Name" finding. With "Allow config changes",
"Apply naming plan" on the Findings page writes the names, after checking each device's identity.

## Peer comparison

`peer_ignore` lists paths that may differ per device. `ignore` in a profile or override also applies to the peer
comparison. `peer_depends` hides differences that have no effect:

```yaml
peer_depends:
  - {paths: [cloud.server], when: {cloud.enable: true}}
  - {paths: ["input:*.*"], except: ["input:*.enable"], when: {"input:*.enable": true}}
```

A difference only counts if the condition holds on at least one compared device. Read-only values
(`sys.device.fw_id`, `sys.cfg_rev` …) and values managed by the device (`sys.ui_data`, calibration values) never
count. Everything hidden is listed below the table.
