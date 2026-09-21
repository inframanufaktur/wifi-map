#!/usr/bin/env zsh
# Export/import the wifimap SQLite DB (default <project>/db/wifi-map.db).
# Usage:
#   ./db-transfer.sh export [db_path] [dump_path]
#   ./db-transfer.sh import [db_path] [dump_path]

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="${2:-$_SCRIPT_DIR/db/wifi-map.db}"
DUMP_PATH="${3:-wifi-map-$(date +%Y%m%d-%H%M%S).dump.sql}"

case "${1:-}" in
  export)
    sqlite3 "$DB_PATH" ".dump" > "$DUMP_PATH"
    echo "Exported $DB_PATH -> $DUMP_PATH ($(du -h "$DUMP_PATH" | cut -f1))"
    echo "Copy it over, then: $0 import <db_path> $DUMP_PATH"
    ;;
  import)
    if [[ ! -f "$DUMP_PATH" ]]; then
      echo "Dump not found: $DUMP_PATH" >&2
      exit 1
    fi
    rm -f "$DB_PATH"
    sqlite3 "$DB_PATH" < "$DUMP_PATH"
    echo "Imported $DUMP_PATH -> $DB_PATH"
    sqlite3 "$DB_PATH" "PRAGMA integrity_check;"
    ;;
  *)
    echo "Usage: $0 export|import [db_path] [dump_path]" >&2
    exit 1
    ;;
esac
