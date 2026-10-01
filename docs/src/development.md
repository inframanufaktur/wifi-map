---
layout: layouts/base.njk
title: Development
description: Build, verify, maintain generated documentation, and configure the existing Uberspace deployment.
navKey: development
permalink: /development/index.html
---

# Development

Build the application with Rust {{ capabilities.package.rust_requires }} or
newer. Live WiFi measurements require macOS and Xcode Command Line Tools.
Demo mode, storage, CSV, and evaluation also work on other platforms.
Documentation uses Node.js 26, as specified by `.nvmrc` and `package.json`.
Python 3 is used for development checks and the legacy migration script.

## Build and explore

From a repository checkout:

<pre class="command-block" data-copy><code>scripts/cargo build --locked --release
./wifimap --demo
./wifimap eval --demo
npm ci
npm run docs:build
npm run docs:serve</code></pre>

`scripts/cargo` uses the project-local `.tools/` toolchain when available,
otherwise system Cargo. `./wifimap` checks source freshness and builds the
release executable before running it. Demo measurements stay in memory.

## Where to change behavior

| Path | Responsibility |
| --- | --- |
| `src/cli.rs` | Clap commands, options, dispatch, and capability metadata |
| `src/app.rs`, `src/app/` | Survey and evaluation state, keyboard actions, and background work |
| `src/ui.rs`, `src/ui/` | Shared palette, panels, dashboards, details, and help overlays |
| `src/measure.rs`, `src/measure/` | Radio, path, traffic, and throughput backends |
| `src/evaluation.rs` | Ranking and named-walk comparison metrics |
| `src/model.rs`, `src/store.rs` | Readings, SQLite persistence, and CSV compatibility |
| `docs/src/_data/site.json` | Site branding and navigation |
| `docs/src/`, `eleventy.config.js` | Guides, generated references, and site build |

Keep demo mode isolated from live hardware, network probes, and the survey
database. Changes to persisted readings must preserve the existing database
and 41-column CSV contracts; the storage and CLI integration tests cover them.

## Documentation generation

Before every Eleventy build, including watch rebuilds, the build configuration
runs `scripts/cargo run --quiet --locked -- capabilities`. This compiles current
Rust definitions and emits metadata without opening a terminal dashboard.
Metadata failures stop the docs build.

The CLI reference comes from the actual Clap command tree. Walk shortcuts,
signal thresholds, and evaluation metrics come from the same capability output.
When changing survey keys, update `capabilities()` in `src/cli.rs`, the keyboard
handlers, and the help overlay together. The supplementary tables on
[TUI controls](/tui-controls/) describe picker, editor, and evaluation behavior
and need to be updated when those handlers change.

Watch mode monitors Rust sources, `Cargo.toml`, and `Cargo.lock`, as well as
documentation sources. Generated HTML and copied assets go to `docs/_site/`,
which is ignored by Git. Add new guide pages to `site.json` navigation and the
page list in `tests/docs.test.mjs` so rendering and link checks cover them.

## Verify

<pre class="command-block" data-copy><code>scripts/cargo fmt --check
scripts/cargo clippy --locked --all-targets -- -D warnings
scripts/cargo test --locked
scripts/cargo build --locked --release
python3 scripts/smoke_tui.py
python3 -m unittest discover -s tests -p migration_test.py
npm run docs:build
npm run docs:test</code></pre>

The documentation tests check deterministic metadata, generated references,
rendered versions, active navigation, unique element IDs, internal links, and
local assets. Build the site before running them.

## Uberspace deployment

The existing [documentation workflow]({{ site.repository }}/blob/main/.github/workflows/docs.yml)
verifies the application on macOS and Ubuntu and builds and tests the site on
Ubuntu. Production deployment runs from `main` after both verification jobs
pass, using the GitHub environment named `prod`.

Configure these environment secrets:

| Secret | Value |
| --- | --- |
| `UBERSPACE_SSH_KEY` | Dedicated, unencrypted private deployment key |
| `UBERSPACE_SSH_KNOWN_HOSTS` | Verified SSH known-hosts entry for the server |
| `UBERSPACE_HOST` | Uberspace host name |
| `UBERSPACE_SSH_USER` | Uberspace account name |

Set the environment variable `UBERSPACE_WEB_ROOT` to the domain directory, such
as `wifimap.inframanufaktur.org`, and authorize the public key on Uberspace.
The destination is `/var/www/virtual/<user>/<web-root>/`.

The deployment job rebuilds the site from the verified commit and mirrors
`docs/_site/` with `rsync --delete`. Use a dedicated destination directory:
files absent from the new build are removed. Deployment does not require a
`DEPLOY_DOCS` toggle in this repository.
