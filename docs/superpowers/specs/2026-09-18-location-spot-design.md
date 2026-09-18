# Location > Spot data model — design (2026-09-18)

## Problem
`locations(id,name,floor,outdoors)` currently stores room/spot rows
(kitchen, office, garden) but calls them locations. No parent layer
for physical place (home, office building). Same spot name cannot
safely repeat across sites. Floor/outdoors semantics tied to wrong layer.

## Decision
Two-level hierarchy: `location > spot`.
- `location` = physical place. Bare: `id + name`. Example: home, office.
- `spot` = room/place inside location. Holds `floor + outdoors`.
  Example: kitchen, garden-balcony.
- Fresh break migration: drop old tables, no backfill. User approved.

## Schema (store.py)
```sql
CREATE TABLE locations(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE spots(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0,1)),
  UNIQUE(location_id, name, floor)
);
CREATE TABLE readings(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
  spot_id INTEGER NOT NULL REFERENCES spots(id),
  ssid,bssid,rssi,noise,snr,channel,phy,tx_rate,
  ping_ms,down_mbps,up_mbps,server,note
);
CREATE INDEX idx_readings_spot ON readings(spot_id);
CREATE INDEX idx_readings_ssid ON readings(ssid);
CREATE INDEX idx_spots_location ON spots(location_id);
```

Dataclasses: `Location(id,name)`, `Spot(id,location_id,name,floor,outdoors)`.
`Reading.spot_id` replaces `location_id`.

Helpers:
- location: `create_location(name)`, `list_locations()`,
  `get_location(id)`, `resolve_location(id|name)` auto-create.
- spot: `create_spot(location_id,name,floor,outdoors)`,
  `list_spots(location_id?)`, `get_spot(id)`,
  `resolve_spot(location_id, name, floor, outdoors)`,
  `update_spot_floor(spot_id, floor)`.
- `list_readings(location?, spot?, floor?, ssid?, limit?)` joins
  readings->spots->locations, returns `location_name, spot_name, floor, outdoors`.

## CLI
- `locations list|add --name` (no floor/outdoors).
- `spots list --location ID|NAME | add --location ID|NAME --name X --floor N [--outdoors]`.
- `scan --location REQ --spot REQ [--spot-floor N] [--spot-outdoors 0|1]`.
  Resolve location, then resolve/create spot inside it.
- `walk --location preset`: preset = location. `s/l/n` picker lists
  spots inside active location only. `f` edits spot floor.
- `list/export --location --spot --floor --ssid`. Export columns:
  `id,ts,spot_id,location_name,spot_name,floor,outdoors,ssid,...`.

## TUI
`WalkState`: `active_location_id + active_spot_id` (or active spot only
with location fixed at startup). Picker prefill/cursor logic unchanged,
data source switches from `list_locations()` to `list_spots(location_id)`.
Create prompt adds spot inside current location. No cross-location picker.

## Error handling
- Empty location/spot name -> ValueError, exit 3.
- Unknown location id -> ValueError, exit 3.
- Same spot name + floor inside one location -> IntegrityError.
- Same spot name across locations -> allowed (scoped UNIQUE).
- FK violation on bad spot_id -> IntegrityError.

## Testing
- Rewrite `test_store.py`: location CRUD, spot scoping, readings join.
- CLI tests: spot scoping, required `--location --spot` on scan,
  `spots list/add`, list/export filters.
- TUI tests: picker scoped to location, floor edit targets spot.

## Out of scope
- Migration/backfill of old DBs.
- Address/GPS fields on location.
- Nesting deeper than 2 levels.
- Rename of `outdoors` semantics.

## Self-review
- No TBD/TODO. Scope single plan. Naming `location > spot` used
  consistently. Fresh break removes migration ambiguity. UNIQUE scoping
  explicit. CLI flags explicit, no slash syntax.
