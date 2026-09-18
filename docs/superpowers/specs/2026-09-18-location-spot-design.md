# Location > Room > Spot data model — design (2026-09-18, rev2)

## Problem
`locations(id,name,floor,outdoors)` currently stores room rows
(kitchen, office, garden) but calls them locations. No parent layer
for physical place, no fine-grain point for measurement position
(window, bed, corner). Planning new network cover needs
position-level data to read/analyse.

## Decision
Three-level hierarchy: `location > room > spot`.
- `location` = physical place. Bare: `id + name`. Example: home, office.
- `room` = room/place inside location. Holds `floor + outdoors`.
  Example: kitchen, garden-balcony.
- `spot` = precise measurement point inside room. Bare: `id + name`.
  Example: window, bed, corner, desk.
- Fresh break migration: drop old tables, no backfill. User approved.

## Schema (store.py)
```sql
CREATE TABLE locations(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE rooms(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0,1)),
  UNIQUE(location_id, name, floor)
);
CREATE TABLE spots(
  id INTEGER PRIMARY KEY,
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  name TEXT NOT NULL,
  UNIQUE(room_id, name)
);
CREATE TABLE readings(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
  spot_id INTEGER NOT NULL REFERENCES spots(id),
  ssid,bssid,rssi,noise,snr,channel,phy,tx_rate,
  ping_ms,down_mbps,up_mbps,server,note
);
CREATE INDEX idx_rooms_location ON rooms(location_id);
CREATE INDEX idx_spots_room ON spots(room_id);
CREATE INDEX idx_readings_spot ON readings(spot_id);
CREATE INDEX idx_readings_ssid ON readings(ssid);
```

Dataclasses: `Location(id,name)`, `Room(id,location_id,name,floor,outdoors)`,
`Spot(id,room_id,name)`. `Reading.spot_id` replaces `location_id`.

Helpers:
- location: `create_location(name)`, `list_locations()`,
  `get_location(id)`, `resolve_location(id|name)` auto-create.
- room: `create_room(location_id,name,floor,outdoors)`,
  `list_rooms(location_id?)`, `get_room(id)`,
  `resolve_room(location_id, name, floor, outdoors)`,
  `update_room_floor(room_id, floor)`.
- spot: `create_spot(room_id,name)`, `list_spots(room_id?)`,
  `get_spot(id)`, `resolve_spot(room_id, name)`.
- `list_readings(location?, room?, spot?, floor?, ssid?, limit?)` joins
  readings->spots->rooms->locations, returns
  `location_name, room_name, spot_name, floor, outdoors`.

## CLI
- `locations list|add --name` (bare sites).
- `rooms list --location ID|NAME | add --location ID|NAME --name X --floor N [--outdoors]`.
- `spots list --location ID|NAME --room NAME|ID | add --location ID|NAME --room NAME|ID --name X`.
- `scan --location REQ --room REQ --spot REQ [--room-floor N] [--room-outdoors 0|1]`.
  Resolve location, then room (create with floor/outdoors), then spot.
- `walk --location preset`: preset = location fixed at startup.
  `s/l/n` picker drills room -> spot inside active location.
  `f` edits room floor.
- `list/export --location --room --spot --floor --ssid`. Export columns:
  `id,ts,spot_id,room_id,location_id,location_name,room_name,spot_name,floor,outdoors,ssid,...`.

## TUI
`WalkState`: `active_location_id + active_spot_id`.
Picker: room picker over `list_rooms(location_id)`, then spot picker
over `list_spots(room_id)`; prefill/cursor logic unchanged per level.
Create flows: new room (name/floor/outdoors) or new spot (name in picked room).
`_location_label` prints `#spot location/room/spot (floor N)`.
No cross-location picking.

## Error handling
- Empty location/room/spot name -> ValueError, exit 3.
- Unknown location/room id -> ValueError, exit 3.
- Same room name + floor inside one location -> IntegrityError.
- Same room name across locations -> allowed.
- Same spot name inside one room -> IntegrityError; across rooms -> allowed.
- FK violation on bad spot_id -> IntegrityError.

## Testing
- Rewrite `test_store.py`: location/room/spot CRUD + scoping, readings 3-join.
- CLI tests: room+spot scoping, required `--location --room --spot` on scan,
  `rooms/spots list/add`, list/export filters.
- TUI tests: room->spot drilldown scoped to location, floor edit targets room.

## Out of scope
- Migration/backfill of old DBs.
- Address/GPS fields on location.
- Nesting deeper than 3 levels.

## Self-review
- No TBD/TODO. Naming `location > room > spot` consistent.
  Fresh break removes migration ambiguity. UNIQUE scoping explicit per level.
  Spot bare keeps measurement points cheap to create during walkthrough.
