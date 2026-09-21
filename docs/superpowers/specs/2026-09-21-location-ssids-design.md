# Location-owned SSIDs — Design

## Objective

Replace free-form SSID strings in captured data with location-owned database
entities. Before a new scan or walk starts, the user selects an SSID already
known for that location or creates one from the automatically detected name or
manual input.

Success means:

- one SSID record belongs to exactly one location;
- every new scan and walk has an explicitly selected SSID record;
- readings, walks, and benchmarks reference that record by foreign key;
- existing databases migrate without losing readings or exported SSID names;
- list, export, evaluation, and walk comparison retain their current `ssid`
  string output contract.

## Assumptions and decisions

1. The user's “sdutil” means the existing best-effort automatic identity path
   (`CoreWLAN`, `networksetup`, then `wdutil`).
2. BSSID stays on each reading/benchmark because one SSID can span multiple
   access points.
3. SSID names are case-sensitive, matching Wi-Fi semantics. The database
   enforces `UNIQUE(location_id, name)` without `NOCASE` collation.
4. The existing `--ssid NAME` flag remains supported. It selects an existing
   location SSID by exact name or creates it when absent, providing a
   non-interactive path.
5. Legacy rows whose SSID was null remain unassigned. Legacy walks receive an
   SSID only when all of their assigned readings resolve to one SSID.
6. New captures require an SSID in application code. Foreign-key columns stay
   nullable so legacy unknown data remains representable.
7. Deleting SSIDs is out of scope. Historical entities use restrictive foreign
   keys and cannot become dangling references.

## Data model and contracts

New entity:

```sql
CREATE TABLE ssids(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  UNIQUE(location_id, name)
);
```

Normalized references:

- `readings.ssid_id REFERENCES ssids(id)` replaces `readings.ssid TEXT`.
- `benchmarks.ssid_id REFERENCES ssids(id)` replaces `benchmarks.ssid TEXT`.
- `walks.ssid_id REFERENCES ssids(id)` records the session selection.
- indexes cover `ssids(location_id, name)` and `readings(ssid_id)`.

Store interface:

```python
@dataclass
class SSID:
    id: int
    location_id: int
    name: str

def create_ssid(conn, location_id: int, name: str) -> int: ...
def get_ssid(conn, ssid_id: int) -> Optional[SSID]: ...
def list_ssids(conn, location_id: int) -> List[SSID]: ...
def resolve_ssid(conn, location_id: int, id_or_name: Union[int, str]) -> int: ...
```

`add_reading`, `create_walk`, and `set_benchmark` accept `ssid_id`, validate
that the SSID belongs to the capture location, and reject cross-location IDs.
For compatibility, joined read/query results continue to expose both
`ssid_id` and `ssid` (the joined name). CSV retains its existing `ssid` column
and adds `ssid_id` only if the current export field convention allows additive
columns without breaking tests; otherwise the ID remains database-only.

## Migration

Migration is an explicit, one-off command, not an automatic `get_db` action:

```sh
.venv/bin/python scripts/migrate_ssid_entities.py db/wifi-map.db
```

The script creates a timestamped sibling backup before changing the database,
then advances schema version 1 to 2 in one transaction:

1. Create `ssids`.
2. Insert every distinct nonblank `(location_id, readings.ssid)` pair, deriving
   location through reading → spot → room. Do the same for benchmarks.
3. Rebuild `readings` with `ssid_id`, preserving IDs, timestamps, metrics,
   notes, and walk links.
4. Rebuild `benchmarks` with `ssid_id`.
5. Add `walks.ssid_id`; backfill it only when all non-null readings in that
   walk reference the same SSID.
6. Recreate indexes, run `PRAGMA foreign_key_check`, and set `user_version=2`.

Migration is idempotent. A failure rolls back without changing the original
database or its backup. The application detects a version-1 SSID-string schema
and exits with an actionable error containing the command above; it never
silently rewrites user data at startup. Tests create a real version-1 database
containing duplicate SSID names across locations, null SSIDs, benchmarks, and
walks, then verify every row and relationship after rerunning the script.

## Scan workflow

`scan --location L --room R --spot S` resolves the location first, performs
best-effort automatic detection, then selects an SSID:

1. `--ssid NAME` present: resolve/create that name under `L`, without prompting.
2. Interactive terminal:
   - list existing SSIDs for `L`;
   - preselect a detected SSID when it already exists;
   - otherwise offer `Create detected SSID: NAME`;
   - always offer `Create SSID manually`.
3. Non-interactive terminal without `--ssid`: fail with exit 3 and a concise
   instruction to pass `--ssid`.

Blank manual input is rejected and the selector remains active. Cancelling
before sampling exits without inserting a reading.

## Walk workflow

Walk startup becomes an ordered setup flow:

1. Resolve `--location`, or prompt to select/create a location when omitted.
2. Resolve `--ssid`, or show the same location-scoped SSID selector as scan.
3. Create the walk with both `location_id` and `ssid_id`.
4. Enter the live walk UI. Every snapshot and benchmark uses the selected
   `ssid_id`; live display uses the stored SSID name even when macOS detection
   is redacted.

One walk cannot change SSID. Location/spot selection during the walk remains
scoped to the walk's startup location. Cancelling setup exits without creating
a walk.

## Automatic detection

Detection supplies a creation/selection suggestion only. It never creates a
database row until the user confirms it (unless `--ssid` explicitly names it).
If macOS returns no usable name, the selector shows existing SSIDs and manual
creation without the inaccurate “sudo skipped” message.

## Commands

```sh
# Focused tests
.venv/bin/python -m pytest tests/test_store.py tests/test_cli.py tests/test_tui.py -q

# Full verification
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests
git diff --check

# One-off local migration (creates a timestamped backup)
.venv/bin/python scripts/migrate_ssid_entities.py db/wifi-map.db

# Manual paths
.venv/bin/wifimap scan --location Garde --room R --spot S --no-speedtest
.venv/bin/wifimap walk --location Garde --no-speedtest
```

## Project structure

- `src/wifimap/store.py`: schema v2, legacy-schema detection, SSID CRUD, FK
  validation, compatibility joins.
- `src/wifimap/cli.py`: scan selector and `--ssid` scan flag.
- `src/wifimap/tui.py`: shared selector primitives plus curses/fallback walk
  setup; snapshot and benchmark ID propagation.
- `src/wifimap/evaluation.py`: normalized database join preserving report
  fields.
- `scripts/migrate_ssid_entities.py`: explicit version-1 → version-2 local
  database migration with backup and integrity checks.
- `tests/test_store.py`: entity, integrity, and migration tests.
- `tests/test_cli.py`: scan selection/non-interactive tests.
- `tests/test_tui.py`: walk startup and snapshot association tests.
- `docs/superpowers/specs/`: this contract.

## Code style

Follow the existing Python 3.9-compatible typed store style:

```python
def list_ssids(conn: sqlite3.Connection, location_id: int) -> List[SSID]:
    rows = conn.execute(
        "SELECT id, location_id, name FROM ssids "
        "WHERE location_id = ? ORDER BY id",
        (location_id,),
    ).fetchall()
    return [SSID(id=row[0], location_id=row[1], name=row[2]) for row in rows]
```

Use parameterized SQL, explicit optional types, deterministic ordering, and
existing exit-code conventions. Add no dependency.

## Testing strategy

- Small store tests cover CRUD, uniqueness, blank names, unknown IDs,
  cross-location rejection, and joined compatibility fields.
- Migration integration tests use on-disk SQLite version-1 fixtures and verify
  transactional, idempotent conversion.
- CLI tests fake detection/input and assert selected entity IDs are stored.
- TUI tests exercise pure selector choices and worker propagation without live
  Wi-Fi or curses.
- Existing evaluation/export tests prove the outward `ssid` name contract.
- Manual testing covers the real interactive scan and walk selectors.

## Boundaries

- Always: preserve all legacy data; back up before the one-off migration;
  validate SSID/location ownership; keep SQL parameterized; keep BSSID per
  capture; preserve CSV/report SSID names.
- Ask first: change case-sensitivity, add deletion/rename CRUD, make legacy
  unknown SSIDs mandatory, or remove `--ssid` compatibility.
- Never: infer a location from an SSID name, silently attach an SSID from a
  different location, bypass macOS privacy controls, or drop legacy rows.

## Implementation slices

1. Schema/entity plus explicit migration script with store tests.
2. Normalized read/write paths and evaluation compatibility.
3. Shared SSID selection model and scan workflow.
4. Walk startup selection and worker/benchmark propagation.
5. Full regression run, manual verification, documentation update, review.

## Approval

Approved with explicit one-off local migration replacing automatic startup
migration.
