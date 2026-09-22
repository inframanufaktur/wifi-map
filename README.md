# wifimap

wifimap records WiFi measurements at named spots on macOS. It stores signal,
link, path, and optional throughput data in SQLite.

Requirements: macOS and Python 3.9+. Live signal reads use CoreWLAN through
PyObjC. Readings stay in a local SQLite database unless you export them to CSV.

## Install and take a first reading

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
python -m pip install pyobjc-framework-CoreWLAN

wifimap --db /tmp/demo.db scan --no-speedtest \
  --location HOME --room KITCHEN --spot WINDOW --ssid DEMO-WIFI
```

## Commands

- `wifimap scan` saves one reading.
- `wifimap walk` opens the survey TUI and saves a named walk.
- `wifimap eval` ranks readings and compares walks.
- `wifimap export --csv FILE` exports the database.

Documentation: [quick start](docs/src/quick-start.md),
[Walk TUI](docs/src/walk-tui.md), [evaluation](docs/src/evaluate.md),
[CLI reference](docs/src/cli-reference.md), and
[data and troubleshooting](docs/src/data-troubleshooting.md).

## Build and test the documentation

```sh
npm ci
npm run docs:build
npm run docs:serve
pytest tests/test_docs.py
```

Generated output is written to `docs/_site/` and is not committed. The full
application and documentation suite runs with `pytest`.

## Links

- [Documentation source](docs/src)
- [Repository](https://github.com/inframanufaktur/wifi-map)
- **Production site:** hostname pending; add the deployed URL here after the
  first verified Uberspace deployment.
