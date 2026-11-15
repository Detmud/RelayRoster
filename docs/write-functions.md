# Write functions

## Read-only by default

The RPC client only lets `*.Get*` and `*.List*` methods through (`app/shelly.py`); any other call fails before it
reaches the network. Each write function has its own switch in **Settings**, all off by default:

| Switch | Calls it allows |
|---|---|
| Allow config changes | `<Component>.SetConfig`, only with fields from the allow list in `app/writable.py` (also used for device names, profiles, bulk change and restore) |
| Allow firmware updates | `Shelly.CheckForUpdate` without parameters, `Shelly.Update` with exactly `{"stage": "stable"\|"beta"}` |
| Allow restarting devices | `Shelly.Reboot` without parameters |
| Allow setting device passwords | `Shelly.SetAuth`, only to set a password on devices that have none |

The test suite checks every request sent to the simulated devices against these rules. Every write action is
recorded in the audit log.

**Use at your own risk.** Firmware updates restart devices, and a wrong setting can switch a load or stop a shutter.
Test write functions on a device that is not doing anything important first.

## How writing works

All of them follow the same pattern: preview first, then a background job that handles one device after the other,
checks the device identity (`Shelly.GetDeviceInfo`) before writing, aborts if the device changed since it was last
read, reads the device back, stores a snapshot and writes the audit log.

- **Edit configuration** (device page, Settings tab, "Edit"): changes collect as a draft, "Review & write" shows the
  exact calls (e.g. `Cover.SetConfig {"id": 0, "config": {"power_limit": 280}}`), then they are written. "Use …"
  takes the policy target or the majority of same-type devices into the draft. Writable fields are listed in
  `app/writable.py`. Deliberately not writable: Wi-Fi client settings (a device could lock itself out), passwords,
  `sys.device.profile`, calibration and measured values, scripts, schedules, webhooks and KVS.
- **Fix on all / Apply profile / Bulk change**: write a fixed target value from a finding, a whole profile, or one path
  and value for many devices. The preview shows per device whether a value will change, is already set, is not
  supported or not writable. Bulk change can also record the rule in `policy.yaml` so later drift is reported.
- **Restore**: play a configuration from a backup back onto the same device, with a preview of every path that changes.
- **Firmware updates**: per device, per type or all; `Shelly.CheckForUpdate`, then `Shelly.Update`, then wait for the
  new version (up to 180 s). Beta only when explicitly chosen. Firmware URLs are never accepted.
- **Restart**: `Shelly.Reboot`, confirmed once the device answers again with a lower uptime.
- **Set device password**: rolls the password from Settings out to devices that have none.

Shellies without a device password can be controlled by anyone in the same network. The UI marks them as "open";
`security.require_auth: true` in the policy turns that into findings.
