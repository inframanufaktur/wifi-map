"""SQLite store: locations CRUD + readings insert + join queries."""
from __future__ import annotations

from wifimap.store_access_points import (
    AccessPoint,
    get_access_point_name,
    list_access_points,
    normalize_bssid,
    set_access_point_name,
)
from wifimap.store_benchmarks import (
    clear_benchmark,
    format_benchmark_delta,
    get_benchmark,
    set_benchmark,
)
from wifimap.store_networks import (
    create_location,
    create_ssid,
    create_walk,
    finish_walk,
    get_location,
    get_ssid,
    get_walk,
    list_locations,
    list_ssids,
    list_walks,
    resolve_ssid,
)
from wifimap.store_places import (
    create_room,
    create_spot,
    get_room,
    get_spot,
    list_rooms,
    list_spots,
    lookup_location,
    lookup_room,
    resolve_location,
    resolve_room,
    resolve_spot,
)
from wifimap.store_readings import add_reading, list_readings
from wifimap.store_schema import (
    Location,
    SSID,
    SchemaMigrationRequired,
    Walk,
    get_db,
)
