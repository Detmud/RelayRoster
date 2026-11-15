# Contributing

Thanks for helping with RelayRoster. Issues and pull requests are welcome, small ones especially.

## Before you start

- **Bugs**: open an issue with the device model, its firmware version and the RelayRoster version. The logs of the
  container (`docker compose logs`) usually show what went wrong.
- **New device types**: RelayRoster reads any Shelly Gen2+ device, but the policy schema and the peer comparison
  know specific components. An issue with the output of `/rpc/Shelly.GetConfig` and `/rpc/Shelly.GetStatus` of
  the new device (passwords removed) is the best start.
- **Bigger changes**: open an issue first so we can agree on the approach before you spend time on it.

## Development setup

Python 3.12 or newer, no real devices needed: `tests/fake_shelly.py` simulates a whole fleet.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt ruff
python tools/fetch_vendor.py app/static/vendor    # Tabler CSS, once
bash tests/ci.sh                                   # ruff and all tests
python tests/demo.py 8790                          # the UI with a simulated fleet
```

[docs/development.md](docs/development.md) has the details, [docs/architecture.md](docs/architecture.md) the
overview of the modules.

## Rules for the code

- **Read-only by default.** Every function that writes to a device has its own switch in Settings and is off until
  the user turns it on. A pull request must not add a write path that bypasses this.
- **Allow list for configuration.** RelayRoster may only change the fields listed in `app/writable.py`. Wi-Fi
  credentials, passwords, device profiles and calibration values are deliberately excluded; please do not add them.
- **Every write is audited.** New write functions record what they changed in the audit log (page Activity), like the existing ones.
- **Tests** go next to the code they test (`tests/test_*.py`) and run against the fake fleet. `bash tests/ci.sh`
  must stay green.
- **Language**: code, comments, documentation and commit messages in English.
- **Tables**: every table is sortable by every column that has a heading; columns with only buttons have no
  heading. `sortTables` in `app/static/app.js` does this for any `table.table` with a `<thead>`. Give a cell
  `data-sort` when its text does not sort well (rank of a status) and put times in `timeHtml`/`timeLongHtml`.
- **Product name**: in any text a user reads (UI, docs, messages, page titles) write **RelayRoster**. Lowercase
  `relayroster` only for technical identifiers: image name, file names (`relayroster-backup.zip`), cookies,
  headers, localStorage keys, database file.
- **Dependencies**: pinned in `requirements.txt`; Dependabot proposes updates.

## Pull requests

- One topic per pull request, with a short description of what changes and why.
- Keep the changelog current: add a line under `## [Unreleased]` in `CHANGELOG.md`.
- The CI runs ruff, the tests and a build of the container image. All three must pass.

## Licence

By contributing you agree that your contribution is licensed under the [MIT licence](LICENSE) of the project.
