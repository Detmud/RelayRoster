# Development

```bash
python tools/fetch_vendor.py                      # once: Tabler CSS into app/static/vendor/
bash tests/ci.sh                                  # ruff and all tests in a fresh venv
python tests/demo.py                              # UI with a simulated fleet on http://127.0.0.1:8746
python tests/demo.py --slow-scan                  # same, "Scan network" then takes about a minute
ALLOW_CONFIG=true ALLOW_FIRMWARE_UPDATE=true ALLOW_REBOOT=true python tests/demo.py   # with write functions
```

`tests/fake_shelly.py` simulates a fleet (Plus i4, Plus 1, 2PM Gen4 as cover, 1 Mini Gen3 with password and a Gen1
device that must be ignored), including paginated script code, firmware updates and restarts. No real devices are
needed for the tests. See [architecture.md](architecture.md) for an overview of the code and
[testing-real-devices.md](testing-real-devices.md) for a checklist against real hardware. How releases and images are published: [releasing.md](releasing.md).

The local RPC API of the devices is documented by Shelly at
[shelly-api-docs.shelly.cloud](https://shelly-api-docs.shelly.cloud/gen2/).
