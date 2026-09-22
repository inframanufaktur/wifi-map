# Spec: Generated documentation site

## Objective

Build a fast, accessible static website that makes wifimap's real capabilities
easy to understand and keeps its reference material synchronized with the
application.

The site is for someone evaluating, installing, or actively using wifimap. It
should answer these questions in order:

1. What problem does wifimap solve?
2. What can I do in the walk and evaluation TUIs?
3. How do I install it and complete a first survey?
4. What commands, options, controls, metrics, and data does it support?

The README becomes a short project entry point. Detailed user documentation
moves to the site so the repository no longer presents one long wall of text.

## Product requirements

### Information architecture

The generated site contains these pages:

- **Overview** — concise product explanation, platform requirements, and a
  TUI-styled visual showing the capture → compare → evaluate workflow.
- **Quick start** — install, first snapshot, first named walk, comparison, and
  evaluation with copyable commands.
- **Walk TUI** — live signal, link, path, traffic, access-point, benchmark,
  snapshot, throughput, room/spot, and prior-walk comparison capabilities.
- **Evaluate** — scope selection, per-spot summaries, individual readings,
  metric ranking, walk comparison, coverage modes, and detail views.
- **CLI reference** — command tree, options, defaults, choices, and help text
  generated from `wifimap.cli._build_parser()`.
- **Data and troubleshooting** — storage model, CSV shape, privacy behavior,
  optional integrations, exit codes, and common failure recovery.

The primary narrative is organized around user workflows, not around source
modules or a flat command list.

### Capability generation

`npm run docs:build` builds the complete publish directory at `docs/_site/`.
Eleventy renders the site, while a small Python extractor uses application
code as the source of truth:

- CLI commands and arguments come from the live `argparse` parser.
- Walk keys come from `wifimap.walk_keys` and the walk footer formatter.
- Walk status labels, thresholds, and chart glyphs come from
  `wifimap.walk_ui`.
- Evaluation scopes and metrics come from `wifimap.eval_state` and
  `wifimap.evaluation`.
- Package name, version, Python requirement, and optional dependencies come
  from the installed package metadata produced from `pyproject.toml`.

The extractor serializes a deterministic JSON document to standard output.
An Eleventy JavaScript global-data file invokes it with a fixed executable and
argument list, parses the JSON, and exposes it to Markdown and Nunjucks
templates. Eleventy also watches the relevant Python source directories, so a
capability change triggers extraction and rendering during local development.
No intermediate capability file is committed.

Explanatory prose and examples remain explicitly authored as Markdown under
`docs/src/`; an extractor cannot infer intent, caveats, or a useful learning
sequence from an argument parser. Generated reference data and authored
guidance must be kept visually distinct so readers know which content is
guaranteed to follow code.

The build is deterministic: the same checkout produces byte-for-byte identical
HTML, CSS, and JavaScript, with no timestamp or environment-dependent output.

### Visual language

The website adapts the application's TUI language rather than introducing a
separate generic documentation theme:

- dark terminal surface and system monospace type;
- cyan accent for section/instrument labels;
- green, yellow, and red semantic treatment for GREAT, OK, and WEAK states;
- compact uppercase labels, middle-dot separators, block/sparkline charts,
  bordered terminal panels, and reverse-video navigation/key bars;
- information-dense desktop layouts that collapse into a clear single column
  on narrow screens.

The result should feel like the TUI expressed as a website, not like a fake
terminal pasted into a marketing template. Decorative terminal effects must
not reduce readability.

### Accessibility and responsive behavior

- Semantic landmarks, one `h1` per page, and unskipped heading levels.
- Visible skip link and focus treatment.
- All navigation and copy controls usable with a keyboard.
- Text contrast meets WCAG 2.1 AA; status is never communicated by color
  alone.
- Content remains usable at 320, 768, 1024, 1440, 2440 CSS pixels.
- Motion is nonessential and disabled by `prefers-reduced-motion`.
- JavaScript is progressive enhancement only; all documentation is readable
  when scripts are disabled.
- Typography and spacing are fluid, utilising utopia.fyi capped between 320 and 2440 px

### README

Replace the existing long-form README with a compact entry point containing:

- what wifimap is and its macOS/Python requirements;
- a minimal install and first-run example;
- the three core workflows;
- local docs build/test commands;
- links to the generated docs source, repository, and deployed site once its
  hostname is known.

Implementation and contributor details that remain useful should move into an
appropriate site page rather than being silently discarded.

### Continuous integration and deployment

Add one GitHub Actions workflow. It installs the pinned Python and Node
toolchains, uses `npm ci`, and invokes the same npm build command used locally:

- **Pull requests targeting `main`:** install the Python project, run the test
  suite, build the docs, and run docs-specific validation. No Uberspace secrets
  are exposed and no deployment occurs.
- **Pushes to `main` (including merged pull requests):** run the same gates,
  then deploy `docs/_site/` to the production Uberspace DocumentRoot.
- **Manual dispatch:** allow a maintainer to rerun the verified production
  deployment when required.
- **Concurrency:** allow at most one production docs deployment and cancel a
  superseded pending/running deployment.

Following the supplied production example, deployment uses the runner's
OpenSSH and rsync clients with these GitHub `prod` environment secrets:

- `UBERSPACE_SSH_KEY` — a dedicated private deploy key;
- `UBERSPACE_HOST` — the account host, such as `stardust.uberspace.de`;
- `UBERSPACE_SSH_USER` — the Uberspace account name; and
- `UBERSPACE_SSH_KNOWN_HOSTS` — the host's independently verified SSH host-key
  line.

The deploy command mirrors the generated directory into the Uberspace default
DocumentRoot and enforces the permissions Uberspace requires:

```sh
rsync -avz --delete --chmod=D755,F644 -e "ssh -i ~/.ssh/deploy_key" \
  docs/_site/ \
  "$UBERSPACE_SSH_USER@$UBERSPACE_HOST:/var/www/virtual/$UBERSPACE_SSH_USER/html/"
```

The workflow declares the GitHub `prod` environment. It does not create
the Uberspace account, domain, DocumentRoot, or SSH key. Because `--delete`
makes the remote site an exact mirror, that `html` directory must be dedicated
to these docs. A maintainer should restrict the deploy key server-side to this
one directory with Uberspace's documented `rrsync` setup.

## Tech stack

- Python 3.9+ and the standard library for capability extraction.
- Node.js 26 and lockfile-pinned Eleventy 3.1.6 for static generation, Markdown
  content, Nunjucks layouts, data binding, local watching, and the dev server.
- Semantic HTML, hand-authored CSS, and minimal dependency-free browser
  JavaScript.
- Existing `pytest` test stack for extractor and contract tests.
- OpenSSH and rsync for deployment to Uberspace.
- GitHub Actions for verification and production deployment.

No client-side framework, Eleventy plugin, remote font, analytics script, or
runtime API is required. Eleventy is a build-time dependency only; the
published result remains dependency-free static HTML, CSS, and JavaScript.

## Commands

```sh
# Install the application and test dependency
python -m pip install --upgrade pip
python -m pip install -e '.[test]'

# Install the pinned docs toolchain
npm ci

# Build docs
npm run docs:build

# Build, watch capability/content sources, and serve locally
npm run docs:serve

# Run all project tests, including docs contracts
pytest

# Run only docs tests
pytest tests/test_docs.py

# Production deploy (CI only by default)
rsync -avz --delete --chmod=D755,F644 -e "ssh -i ~/.ssh/deploy_key" \
  docs/_site/ \
  "$UBERSPACE_SSH_USER@$UBERSPACE_HOST:/var/www/virtual/$UBERSPACE_SSH_USER/html/"
```

## Project structure

```text
docs/
  extract_capabilities.py  deterministic application-to-JSON adapter
  src/
    _data/
      capabilities.js      invokes the Python extractor for Eleventy
      site.json             authored global site metadata/navigation
    _includes/
      layouts/
        base.njk            shared semantic document shell
      partials/             focused Nunjucks presentation components
    assets/
      site.css              TUI-derived tokens and responsive layout
      site.js               navigation/copy progressive enhancement
    *.md                    authored pages, examples, and caveats
  specs/
    static-docs-site.md    this specification
  _site/                   generated output; ignored by git
tests/
  test_docs.py             extractor, reference, link, and HTML contracts
.github/workflows/
  docs.yml                 verification and production Uberspace deployment
.nvmrc                     pinned local/CI Node major version
eleventy.config.js         input/output, passthrough, and Python watch targets
package.json               local docs commands and Eleventy dependency
package-lock.json          reproducible Node dependency graph
```

Generated HTML is not committed. CI always builds it from the same application
checkout it deploys.

## Code style

Extractor code stays small, typed with Python 3.9-compatible annotations, and
uses explicit immutable records for reference data. It has no presentation
logic. Nunjucks autoescaping remains enabled for application-derived text, and
templates do not use `safe` on extracted values.

```python
@dataclass(frozen=True)
class CommandDoc:
    path: tuple[str, ...]
    summary: str
    options: tuple[OptionDoc, ...]


def command_docs(parser: argparse.ArgumentParser) -> tuple[CommandDoc, ...]:
    """Return a deterministic, presentation-neutral command tree."""
```

The Node bridge executes a fixed local command rather than interpolating data
into a shell command:

```js
const raw = execFileSync(python, ["docs/extract_capabilities.py"], {
  encoding: "utf8",
});

export default JSON.parse(raw);
```

CSS uses semantic custom properties such as `--surface`, `--accent`,
`--status-great`, and `--status-weak`; page markup does not embed raw colors or
inline styles. JavaScript does not generate documentation content.

## Testing strategy

- **Unit tests:** the Python capability extractor covers nested commands, required
  options, flags, choices, defaults, and hidden/internal parser actions.
- **Contract tests:** generated CLI commands equal the paths exposed by the
  live parser; walk controls and evaluation metrics rendered on the site equal
  their application sources.
- **Build tests:** run the production Eleventy build, verify every planned page
  and static asset, validate internal links/fragment targets, reject unescaped
  application data, and confirm a second build is identical.
- **Repository tests:** the full existing `pytest` suite remains green.
- **Browser verification:** serve `docs/_site`, inspect desktop and mobile
  layouts, keyboard navigation, accessibility structure, console output, and
  reduced-motion behavior in an isolated browser profile.

The CI build itself is a release gate: a capability that cannot be rendered
or a broken internal link fails before deployment.

## Boundaries

### Always

- Derive command/reference facts from application code.
- Escape extracted text and keep the build deterministic.
- Preserve Python 3.9 application compatibility.
- Pin Eleventy and its dependency graph with `package-lock.json`.
- Keep extraction in Python and presentation in Eleventy templates.
- Run the full test suite and docs build before deployment.
- Keep Uberspace connection details and key material in GitHub environment
  secrets.
- Match the TUI's visual and semantic language while meeting web accessibility
  requirements.

### Ask first

- Add a client-side framework, Eleventy plugin, or remote asset dependency.
- Change application command behavior solely to simplify documentation.
- Enable PR preview deployments, comments, analytics, or third-party scripts.
- Create, rename, or delete the Uberspace DocumentRoot or configure its domain.
- Remove user-facing documentation without moving its still-current substance.

### Never

- Commit Uberspace keys, account details, host keys, or generated build output.
- Deploy unverified output or deploy from pull-request code with production
  secrets.
- scrape README text to produce capability reference data.
- require JavaScript to read or navigate the documentation.

## Success criteria

- A new user can identify the capture, walk/compare, and evaluation workflows
  from the home page without reading the CLI reference.
- The walk and evaluation pages describe every currently exposed TUI control
  and core instrument.
- Adding/removing a parser command, walk control, rating threshold, or
  evaluation metric changes the next docs build or fails a contract test; it
  cannot silently leave the published reference stale.
- `npm run docs:build` succeeds with Python 3.9+, Node 26, the installed Python
  project, and the lockfile-pinned Node dependencies, producing a fully
  navigable `docs/_site/`.
- `pytest` validates generation, internal links, escaping, determinism, and
  capability coverage while preserving all existing tests.
- The site has no browser console errors or accessibility warnings in manual
  verification and works at all required breakpoints.
- Pull requests verify but do not deploy; pushes to `main` deploy only after
  all gates pass.
- Production credentials and site identity exist only in GitHub/Uberspace
  configuration.
- README is concise and no longer duplicates the long-form docs.

## Open questions and deployment handoff

These do not block implementation:

1. The final Uberspace hostname/custom domain is not known. Keep the README's
   deployed-site link easy to update after the first successful deployment.
2. A maintainer must dedicate the account's default DocumentRoot to the docs
   and add `UBERSPACE_SSH_KEY`, `UBERSPACE_HOST`, `UBERSPACE_SSH_USER`, and
   `UBERSPACE_SSH_KNOWN_HOSTS` to the GitHub `prod` environment before
   the deploy job can succeed.
3. Repository branch protection and required status checks are configured in
   GitHub, not in this change; the workflow will provide the check to require.

## Verified implementation sources

- GitHub recommends `setup-python` for consistent runner behavior:
  https://docs.github.com/en/actions/tutorials/build-and-test-code/python
- GitHub documents `push`, `pull_request`, and `workflow_dispatch` deployment
  triggers, environments, and deployment concurrency:
  https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments
- Uberspace documents rsync-based GitHub Actions deployment and restricting a
  deploy key to one directory with `rrsync`:
  https://lab.uberspace.de/howto_automatic-deployment/
- Uberspace documents its DocumentRoot layout and required directory/file
  permissions:
  https://manual.uberspace.de/web-documentroot/
- The supplied workflow used as the concrete deployment model:
  https://github.com/ovlb/zeichen-guter-gastlichkeit/blob/main/.github/workflows/deploy.yml
- Eleventy 3.1.6 is the current stable release and supports Node 18 or newer:
  https://www.11ty.dev/
- Eleventy exposes JSON and JavaScript global data files to templates:
  https://www.11ty.dev/docs/data-global/
- Eleventy supports explicit Nunjucks environment options, including
  autoescaping:
  https://www.11ty.dev/docs/languages/nunjucks/#nunjucks-environment-options
- Eleventy can watch external source directories and rebuild its development
  server when they change:
  https://www.11ty.dev/docs/watch-serve/#add-your-own-watch-targets
