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
- [ ] Reset the name; both changes are on the Activity page

## 3. Edit components
Toggle once per component, check after read-back, then toggle back:
- [ ] Bluetooth: "Enable" (turn it back on, otherwise BTHome sensors stop working)
- [ ] Outbound WebSocket: "Enable"
- [ ] Matter: "Enable" (Gen4 only; a restart may be required)
- [ ] KNX: "Enable" (only where available)
- [ ] Modbus: "Enable" (only Pro/Gen3/Gen4 with Modbus)

## 4. Restart several devices
- [ ] Bulk change: select two devices, "Restart n devices…"
- [ ] Progress shows both one after the other, both come back online, Activity entry per device

## 5. Roll out the device password
- [ ] Enter a password in Settings, switch on "Allow setting device passwords"
- [ ] Bulk change: select a device without password, "Set password…"
- [ ] The badge changes from "Open" to password set, the device is still readable (Sync)
- [ ] The device's web UI asks for the password; Activity does not contain the password
- [ ] Switch off again

## 6. Restore
- [ ] Change a harmless setting on the test device (e.g. name or Bluetooth observer), Sync
- [ ] Backups tab: older backup, "Restore…"; the preview shows exactly this difference
- [ ] Run the restore; the setting is back, Activity has the entry

## 7. Apply the address plan
Use one device you can reach physically: there is no rollback, and a device that does not come back needs a
factory reset or its access point.
- [ ] Policy: `network` with `subnet`, `dhcp_pool`, `gateway`, `nameserver` and a block that matches the test device;
      the block lies outside the router's DHCP pool
- [ ] Findings shows the device as outside the address plan; "Apply address plan…" lists it with the next free
      address and the netmask, gateway and DNS server from the policy
- [ ] Without "Allow address changes" the button "Move" is disabled; switch it on
- [ ] Move the device: progress shows "setting address", then "waiting for device", then "moved"
- [ ] The device list shows the new address under the same device ID, the finding is gone, Activity has the entry
      (old → new)
- [ ] The device's own web UI answers at the new address and shows a static IP; the old address no longer answers
- [ ] Try to move a second device to an address that is in use (another Shelly): the job refuses, nothing is written
- [ ] Note whether the device took the address at once or asked for a restart
- [ ] Switch "Allow address changes" off again

## 8. Firmware updates in parallel
Needs at least two devices with a stable update available.
- [ ] Settings: "Allow firmware updates" on, "Parallel firmware updates" at 2
- [ ] Firmware: update two or more devices of one type
- [ ] The dialog shows two devices running at the same time; each one walks through check, request, install,
      restart, verify. Note how long a real device stays in "install" and in "restart"
- [ ] "Stop remaining devices" while more are waiting: the running ones finish, the others are skipped
- [ ] All updated devices report the new version, Activity entry per device
- [ ] Switch "Allow firmware updates" off again

## Wrap-up
- [ ] Export Activity as CSV, the entries match the steps
- [ ] Set the switches in Settings to the state you want to keep
