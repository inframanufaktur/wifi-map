# wifimap

A Rust terminal dashboard for named WiFi surveys on macOS. Record radio,
gateway/Internet path quality, and optional throughput measurements in SQLite;
compare spots, ideal-condition benchmarks, and named walks.

```sh
./wifimap --demo       # Explore simulated data without touching your database
./wifimap             # Open the live survey dashboard
./wifimap eval --demo  # Explore rankings and walk comparison
```

The dashboard follows [usmaybe](../usmaybe)'s dark terminal design: turquoise
accents, a place sidebar, signal/path charts, selected-reading details, and
keyboard help. Use a terminal at least 76 columns × 24 rows; 110 × 32 is preferred.
Press `?` for help and `q` or Ctrl+C to quit and restore the terminal.
Quitting cancels unfinished captures and speed probes.

## Install and capture

Install Rust 1.88 or newer and Xcode Command Line Tools. `scripts/cargo` uses an
isolated `.tools/` toolchain when available, otherwise system Cargo. `./wifimap`
builds the release executable on first use and checks for source changes on later
runs. To install a standalone executable, use `scripts/cargo install --locked --path .`.

Live measurements use native CoreWLAN with a `system_profiler` fallback. Python,
PyObjC, and a virtual environment are not required. Optional Ookla `speedtest`
provides throughput; use `--no-speedtest` for signal-only captures.

```sh
./wifimap --db /tmp/survey.db scan --no-speedtest \
  --location HOME --room KITCHEN --spot WINDOW --ssid HOME-WIFI

./wifimap walk --location HOME --ssid HOME-WIFI --name before-install
./wifimap walk --location HOME --ssid HOME-WIFI --name after-install \
  --compare-to before-install
./wifimap eval
./wifimap export --csv readings.csv
```

`s` saves a snapshot, `t` runs a temporary speed probe, `b` captures a location
benchmark, and `c` selects a previous walk to compare. `l`, `r`, and `p` open the
location, room, and spot pickers. `n` creates a place and `a` names an access point.

## Existing data

Normalized SQLite databases from the Python application open directly. The
default remains `db/wifi-map.db` in the checkout. Installed binaries use that
relative path in the working directory; `--db` overrides it. Parent directories
are created for new survey databases. CSV retains the existing 41-column format.

Older databases with an SSID text column still require the one-off maintenance
script, which makes a backup before migrating:

```sh
python3 scripts/migrate_ssid_entities.py db/wifi-map.db
```

The application runs entirely in Rust. Readings stay local unless exported.
Path probes send ICMP to the router, 1.1.1.1, and 8.8.8.8; Ookla tests use its
external servers. Demo data is isolated in memory. Non-macOS builds support
demo, storage, CSV, and evaluation.

## Build and verify

```sh
scripts/cargo test --locked
scripts/cargo fmt --check
scripts/cargo clippy --locked --all-targets -- -D warnings
scripts/cargo build --locked --release
python3 scripts/smoke_tui.py
python3 -m unittest discover -s tests -p migration_test.py
npm ci
npm run docs:build
npm run docs:test
```

Tests cover legacy database/CSV compatibility, validation, rankings/comparisons,
measurement parsing, background state, terminal rendering, and CLI workflows.
The PTY smoke test uses Python's standard library as development tooling.
Documentation capabilities are generated from the Rust command tree.

Documentation: [quick start](docs/src/quick-start.md),
[walk TUI](docs/src/walk-tui.md), [evaluation](docs/src/evaluate.md),
[CLI reference](docs/src/cli-reference.md), and
[data and troubleshooting](docs/src/data-troubleshooting.md).

[Repository](https://github.com/inframanufaktur/wifi-map) ·
[Documentation](https://wifimap.inframanufaktur.org/)
