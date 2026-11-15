# Checklist: write functions on real devices

The automated tests run against simulated devices only. Before a release, go through these steps on real hardware.
Pick a test device that is not switching anything important (no moving shutter, no critical load), e.g. a
1 Mini Gen3. Report problems with device model, firmware version and the step.

## Preparation
- [ ] Settings: "Allow config changes" and "Allow restarting devices" on; "Allow setting device passwords" only for step 5
- [ ] Sync; the overview of the test device then shows the number of RPC methods

## 1. Capability detection via `Shelly.ListMethods`
- [ ] Settings tab: Wi-Fi and MQTT have "Edit"
- [ ] A device without Bluetooth/Modbus (if available) shows no "Edit" there

## 2. Device name via Settings
- [ ] Settings → System → change "Device name", review, write
- [ ] The name appears in the page header, the device list and the device's own web UI
- [ ] Reset the name; both changes are in the audit log

## 3. Edit components
Toggle once per component, check after read-back, then toggle back:
- [ ] Bluetooth: "Enable" (turn it back on, otherwise BTHome sensors stop working)
- [ ] Outbound WebSocket: "Enable"
- [ ] Matter: "Enable" (Gen4 only; a restart may be required)
- [ ] KNX: "Enable" (only where available)
- [ ] Modbus: "Enable" (only Pro/Gen3/Gen4 with Modbus)

## 4. Restart several devices
- [ ] Bulk change: select two devices, "Restart n devices…"
- [ ] Progress shows both one after the other, both come back online, audit log entry per device

## 5. Roll out the device password
- [ ] Enter a password in Settings, switch on "Allow setting device passwords"
- [ ] Bulk change: select a device without password, "Set password…"
- [ ] The badge changes from "Open" to password set, the device is still readable (Sync)
- [ ] The device's web UI asks for the password; the audit log does not contain the password
- [ ] Switch off again

## 6. Restore
- [ ] Change a harmless setting on the test device (e.g. name or Bluetooth observer), Sync
- [ ] Backups tab: older backup, "Restore…"; the preview shows exactly this difference
- [ ] Run the restore; the setting is back, the audit log has the entry

## Wrap-up
- [ ] Export the audit log as CSV, the entries match the steps
- [ ] Set the switches in Settings to the state you want to keep
