# wifimap

wifimap is a local-first home WiFi survey tool. Capture room- and spot-tagged
signal, link, path, and optional throughput readings; compare named walks after
an infrastructure change; then rank weak coverage in a terminal dashboard.

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

## Core workflows

- **Capture:** `wifimap scan` saves one snapshot; `wifimap walk` surveys a
  route with live signal, path, traffic, access-point, and benchmark context.
- **Compare:** name walks and pass `--compare-to` to see current readings beside
  a prior route, or compare two completed walks in evaluation.
- **Evaluate:** `wifimap eval` ranks spot summaries or individual readings and
  opens exact radio, throughput, path, and walk-comparison details.

Start with the [quick start](docs/src/quick-start.md), then use the
[Walk TUI guide](docs/src/walk-tui.md), [evaluation guide](docs/src/evaluate.md),
[generated CLI reference](docs/src/cli-reference.md), and
[data and troubleshooting guide](docs/src/data-troubleshooting.md).

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
