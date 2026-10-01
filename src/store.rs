//! Normalized SQLite persistence, compatible with application schema v4.
use crate::model::{PathFields, Reading, Signal, Speed, timestamp};
use anyhow::{Context, Result, bail, ensure};
use rusqlite::{Connection, OptionalExtension, Row, params, types::Value};
use std::path::Path;

#[derive(Debug)]
pub struct Store {
    conn: Connection,
}

#[derive(Debug, Clone, Default)]
pub struct Filter {
    pub location: Option<String>,
    pub room: Option<String>,
    pub spot: Option<String>,
    pub ssid: Option<String>,
    pub floor: Option<i64>,
    pub limit: Option<usize>,
    pub walk_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Location {
    pub id: i64,
    pub name: String,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Room {
    pub id: i64,
    pub location_id: i64,
    pub name: String,
    pub floor: i64,
    pub outdoors: bool,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spot {
    pub id: i64,
    pub room_id: i64,
    pub name: String,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ssid {
    pub id: i64,
    pub location_id: i64,
    pub name: String,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Walk {
    pub id: i64,
    pub location_id: i64,
    pub name: String,
    pub started_at: String,
    pub ended_at: Option<String>,
    pub ssid_id: Option<i64>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccessPoint {
    pub bssid: String,
    pub name: String,
}
pub type Benchmark = Reading;

impl Store {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("create database directory {}", parent.display()))?;
        }
        Self::initialize(
            Connection::open(path).with_context(|| format!("open database {}", path.display()))?,
        )
    }

    pub fn memory() -> Result<Self> {
        Self::initialize(Connection::open_in_memory()?)
    }

    /// Evaluation opens an existing database without creating or migrating it.
    pub fn open_readonly(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        ensure!(
            path.is_file(),
            "database does not exist: {}",
            path.display()
        );
        let conn = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .with_context(|| format!("cannot read database {}", path.display()))?;
        let cols = columns(&conn, "readings")?;
        ensure!(!cols.is_empty(), "database has no readings table");
        ensure!(
            cols.iter().any(|s| s == "ssid_id" || s == "ssid"),
            "database has no supported SSID field"
        );
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        Ok(Self { conn })
    }

    fn initialize(conn: Connection) -> Result<Self> {
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        let cols = columns(&conn, "readings")?;
        if cols.iter().any(|s| s == "ssid") && !cols.iter().any(|s| s == "ssid_id") {
            bail!(
                "legacy SSID schema; run python3 scripts/migrate_ssid_entities.py <database> before opening it"
            );
        }
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;
        conn.execute_batch(SCHEMA)?;
        let cols = columns(&conn, "readings")?;
        for (name, kind) in PATH_COLUMNS {
            if !cols.iter().any(|s| s == name) {
                conn.execute_batch(&format!("ALTER TABLE readings ADD COLUMN {name} {kind}"))?;
            }
        }
        if !cols.iter().any(|s| s == "walk_id") {
            conn.execute_batch("ALTER TABLE readings ADD COLUMN walk_id INTEGER REFERENCES walks(id) ON DELETE SET NULL")?;
        }
        if !columns(&conn, "walks")?.iter().any(|s| s == "ssid_id") {
            conn.execute_batch(
                "ALTER TABLE walks ADD COLUMN ssid_id INTEGER REFERENCES ssids(id)",
            )?;
        }
        conn.execute_batch(
            "CREATE INDEX IF NOT EXISTS idx_readings_walk_spot ON readings(walk_id, spot_id)",
        )?;
        let version: i64 = conn.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if version < 4 {
            conn.pragma_update(None, "user_version", 4)?;
        }
        Ok(Self { conn })
    }

    pub fn create_location(&self, name: &str) -> Result<i64> {
        ensure!(!name.is_empty(), "location name must not be empty");
        self.conn
            .execute("INSERT INTO locations(name) VALUES (?)", [name])?;
        Ok(self.conn.last_insert_rowid())
    }
    pub fn list_locations(&self) -> Result<Vec<Location>> {
        collect(
            &self.conn,
            "SELECT id,name FROM locations ORDER BY id",
            [],
            location_row,
        )
    }
    pub fn get_location(&self, id: i64) -> Result<Option<Location>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id,name FROM locations WHERE id=?",
                [id],
                location_row,
            )
            .optional()?)
    }
    pub fn lookup_location(&self, name: &str) -> Result<i64> {
        if let Ok(id) = name.trim().parse::<i64>()
            && self.get_location(id)?.is_some()
        {
            return Ok(id);
        }
        self.conn
            .query_row("SELECT id FROM locations WHERE name=?", [name], |r| {
                r.get(0)
            })
            .optional()?
            .with_context(|| format!("unknown location: {name:?}"))
    }
    pub fn resolve_location(&self, name: &str) -> Result<i64> {
        match self.lookup_location(name) {
            Ok(id) => Ok(id),
            Err(_) => self.create_location(name),
        }
    }
    fn require_location(&self, id: i64) -> Result<()> {
        ensure!(
            self.get_location(id)?.is_some(),
            "unknown location id: {id}"
        );
        Ok(())
    }
    pub fn create_room(
        &self,
        location: i64,
        name: &str,
        floor: i64,
        outdoors: bool,
    ) -> Result<i64> {
        self.require_location(location)?;
        ensure!(!name.is_empty(), "room name must not be empty");
        self.conn.execute(
            "INSERT INTO rooms(location_id,name,floor,outdoors) VALUES (?,?,?,?)",
            params![location, name, floor, outdoors],
        )?;
        Ok(self.conn.last_insert_rowid())
    }
    pub fn list_rooms(&self, location: i64) -> Result<Vec<Room>> {
        collect(
            &self.conn,
            "SELECT id,location_id,name,floor,outdoors FROM rooms WHERE location_id=? ORDER BY id",
            [location],
            room_row,
        )
    }
    pub fn get_room(&self, id: i64) -> Result<Option<Room>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id,location_id,name,floor,outdoors FROM rooms WHERE id=?",
                [id],
                room_row,
            )
            .optional()?)
    }
    pub fn lookup_room(&self, location: i64, name: &str) -> Result<i64> {
        if let Ok(id) = name.trim().parse::<i64>()
            && self
                .get_room(id)?
                .is_some_and(|r| r.location_id == location)
        {
            return Ok(id);
        }
        self.conn
            .query_row(
                "SELECT id FROM rooms WHERE location_id=? AND name=? ORDER BY id LIMIT 1",
                params![location, name],
                |r| r.get(0),
            )
            .optional()?
            .with_context(|| format!("unknown room: {name:?}"))
    }
    pub fn resolve_room(
        &self,
        location: i64,
        name: &str,
        floor: i64,
        outdoors: bool,
    ) -> Result<i64> {
        if let Ok(id) = name.trim().parse::<i64>()
            && self
                .get_room(id)?
                .is_some_and(|r| r.location_id == location)
        {
            return Ok(id);
        }
        let found: Option<i64> = self
            .conn
            .query_row(
                "SELECT id FROM rooms WHERE location_id=? AND name=? AND floor=?",
                params![location, name, floor],
                |r| r.get(0),
            )
            .optional()?;
        match found {
            Some(id) => Ok(id),
            None => self.create_room(location, name, floor, outdoors),
        }
    }
    pub fn create_spot(&self, room: i64, name: &str) -> Result<i64> {
        ensure!(self.get_room(room)?.is_some(), "unknown room id: {room}");
        ensure!(!name.is_empty(), "spot name must not be empty");
        self.conn.execute(
            "INSERT INTO spots(room_id,name) VALUES (?,?)",
            params![room, name],
        )?;
        Ok(self.conn.last_insert_rowid())
    }
    pub fn list_spots(&self, room: i64) -> Result<Vec<Spot>> {
        collect(
            &self.conn,
            "SELECT id,room_id,name FROM spots WHERE room_id=? ORDER BY id",
            [room],
            spot_row,
        )
    }
    pub fn get_spot(&self, id: i64) -> Result<Option<Spot>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id,room_id,name FROM spots WHERE id=?",
                [id],
                spot_row,
            )
            .optional()?)
    }
    pub fn lookup_spot(&self, room: i64, name: &str) -> Result<i64> {
        if let Ok(id) = name.trim().parse::<i64>()
            && self.get_spot(id)?.is_some_and(|s| s.room_id == room)
        {
            return Ok(id);
        }
        self.conn
            .query_row(
                "SELECT id FROM spots WHERE room_id=? AND name=?",
                params![room, name],
                |r| r.get(0),
            )
            .optional()?
            .with_context(|| format!("unknown spot: {name:?}"))
    }
    pub fn resolve_spot(&self, room: i64, name: &str) -> Result<i64> {
        match self.lookup_spot(room, name) {
            Ok(id) => Ok(id),
            Err(_) => self.create_spot(room, name),
        }
    }
    pub fn create_ssid(&self, location: i64, name: &str) -> Result<i64> {
        self.require_location(location)?;
        let name = name.trim();
        ensure!(!name.is_empty(), "SSID name must not be blank");
        self.conn.execute(
            "INSERT INTO ssids(location_id,name) VALUES (?,?)",
            params![location, name],
        )?;
        Ok(self.conn.last_insert_rowid())
    }
    pub fn get_ssid(&self, id: i64) -> Result<Option<Ssid>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id,location_id,name FROM ssids WHERE id=?",
                [id],
                ssid_row,
            )
            .optional()?)
    }
    pub fn list_ssids(&self, location: i64) -> Result<Vec<Ssid>> {
        self.require_location(location)?;
        collect(
            &self.conn,
            "SELECT id,location_id,name FROM ssids WHERE location_id=? ORDER BY id",
            [location],
            ssid_row,
        )
    }
    pub fn resolve_ssid(&self, location: i64, name: &str) -> Result<i64> {
        self.require_location(location)?;
        let name = name.trim();
        ensure!(!name.is_empty(), "SSID name must not be blank");
        if let Ok(id) = name.parse::<i64>()
            && self
                .get_ssid(id)?
                .is_some_and(|s| s.location_id == location)
        {
            return Ok(id);
        }
        let found: Option<i64> = self
            .conn
            .query_row(
                "SELECT id FROM ssids WHERE location_id=? AND name=?",
                params![location, name],
                |r| r.get(0),
            )
            .optional()?;
        match found {
            Some(id) => Ok(id),
            None => self.create_ssid(location, name),
        }
    }
    fn validated_ssid(
        &self,
        location: i64,
        id: Option<i64>,
        name: Option<&str>,
    ) -> Result<Option<i64>> {
        // Detected network names are literal, including all-numeric SSIDs.
        let id = match (id, name) {
            (Some(id), _) => Some(id),
            (None, Some(name)) => {
                let found: Option<i64> = self
                    .conn
                    .query_row(
                        "SELECT id FROM ssids WHERE location_id=? AND name=?",
                        params![location, name.trim()],
                        |r| r.get(0),
                    )
                    .optional()?;
                Some(match found {
                    Some(id) => id,
                    None => self.create_ssid(location, name)?,
                })
            }
            (None, None) => None,
        };
        if let Some(id) = id {
            let ssid = self
                .get_ssid(id)?
                .with_context(|| format!("unknown SSID id: {id}"))?;
            ensure!(
                ssid.location_id == location,
                "reading and SSID belong to a different location"
            );
            if let Some(name) = name {
                ensure!(ssid.name == name, "SSID id and name do not match");
            }
        }
        Ok(id)
    }
    pub fn create_walk(&self, location: i64, ssid_id: Option<i64>, name: &str) -> Result<i64> {
        self.require_location(location)?;
        let name = name.trim();
        ensure!(!name.is_empty(), "walk name must not be blank");
        self.validated_ssid(location, ssid_id, None)?;
        self.conn.execute(
            "INSERT INTO walks(location_id,ssid_id,name,started_at) VALUES (?,?,?,?)",
            params![location, ssid_id, name, timestamp()],
        )?;
        Ok(self.conn.last_insert_rowid())
    }
    pub fn get_walk(&self, id: i64) -> Result<Option<Walk>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id,location_id,name,started_at,ended_at,ssid_id FROM walks WHERE id=?",
                [id],
                walk_row,
            )
            .optional()?)
    }
    pub fn list_walks(&self, location: i64) -> Result<Vec<Walk>> {
        collect(
            &self.conn,
            "SELECT id,location_id,name,started_at,ended_at,ssid_id FROM walks WHERE location_id=? ORDER BY id DESC",
            [location],
            walk_row,
        )
    }
    pub fn resolve_walk(&self, location: i64, name: &str) -> Result<i64> {
        let name = name.trim();
        if let Ok(id) = name.trim().parse::<i64>()
            && self
                .get_walk(id)?
                .is_some_and(|w| w.location_id == location)
        {
            return Ok(id);
        }
        let matches = collect(
            &self.conn,
            "SELECT id FROM walks WHERE location_id=? AND name=? ORDER BY id DESC",
            params![location, name],
            |row| row.get::<_, i64>(0),
        )?;
        ensure!(
            matches.len() <= 1,
            "ambiguous walk name {name:?}; use IDs: {}",
            matches
                .iter()
                .map(i64::to_string)
                .collect::<Vec<_>>()
                .join(", ")
        );
        matches
            .first()
            .copied()
            .with_context(|| format!("unknown walk: {name:?}"))
    }
    pub fn finish_walk(&self, id: i64) -> Result<()> {
        ensure!(self.get_walk(id)?.is_some(), "unknown walk id: {id}");
        self.conn.execute(
            "UPDATE walks SET ended_at=COALESCE(ended_at,?) WHERE id=?",
            params![timestamp(), id],
        )?;
        Ok(())
    }
    pub fn add_reading(&self, reading: &Reading) -> Result<i64> {
        let location: i64 = self
            .conn
            .query_row(
                "SELECT m.location_id FROM spots s JOIN rooms m ON m.id=s.room_id WHERE s.id=?",
                [reading.spot_id],
                |r| r.get(0),
            )
            .optional()?
            .context("unknown reading spot")?;
        let ssid_id =
            self.validated_ssid(location, reading.ssid_id, reading.signal.ssid.as_deref())?;
        if let Some(id) = reading.walk_id {
            let walk = self
                .get_walk(id)?
                .with_context(|| format!("unknown walk id: {id}"))?;
            ensure!(
                walk.location_id == location,
                "walk and reading spot belong to a different location"
            );
            if let (Some(a), Some(b)) = (walk.ssid_id, ssid_id) {
                ensure!(a == b, "walk and reading use a different SSID");
            }
        }
        let ts = if reading.ts.is_empty() {
            timestamp()
        } else {
            reading.ts.clone()
        };
        let r = reading;
        let s = &r.signal;
        let v = &r.speed;
        let p = &r.path;
        self.conn.execute("INSERT INTO readings(ts,spot_id,ssid_id,bssid,rssi,noise,snr,channel,phy,tx_rate,ping_ms,down_mbps,up_mbps,path_probe_count,gateway_rtt_ms,gateway_p95_ms,gateway_loss_pct,gateway_max_outage_ms,internet_rtt_ms,internet_p95_ms,internet_loss_pct,internet_max_outage_ms,server,note,walk_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",params![ts,r.spot_id,ssid_id,s.bssid,s.rssi,s.noise,s.snr,s.channel,s.phy,s.tx_rate,v.ping_ms,v.down_mbps,v.up_mbps,p.path_probe_count,p.gateway_rtt_ms,p.gateway_p95_ms,p.gateway_loss_pct,p.gateway_max_outage_ms,p.internet_rtt_ms,p.internet_p95_ms,p.internet_loss_pct,p.internet_max_outage_ms,v.server,r.note,r.walk_id])?;
        Ok(self.conn.last_insert_rowid())
    }
    pub fn list_readings(&self, filter: &Filter) -> Result<Vec<Reading>> {
        let mut sql = self.reading_query()?;
        let mut clauses = Vec::new();
        let mut values = Vec::<Value>::new();
        for (filter, id_col, name_col) in [
            (&filter.location, "m.location_id", "l.name"),
            (&filter.room, "s.room_id", "m.name"),
            (&filter.spot, "r.spot_id", "s.name"),
        ] {
            if let Some(name) = filter {
                if let Ok(id) = name.trim().parse::<i64>() {
                    clauses.push(format!("({id_col}=? OR {name_col}=?)"));
                    values.push(Value::Integer(id));
                } else {
                    clauses.push(format!("{name_col}=?"));
                }
                values.push(Value::Text(name.clone()));
            }
        }
        if let Some(ssid) = &filter.ssid {
            let normalized = columns(&self.conn, "readings")?
                .iter()
                .any(|c| c == "ssid_id");
            clauses.push(if normalized { "n.name=?" } else { "r.ssid=?" }.into());
            values.push(Value::Text(ssid.clone()));
        }
        if let Some(floor) = filter.floor {
            clauses.push("m.floor=?".into());
            values.push(Value::Integer(floor));
        }
        if let Some(walk) = filter.walk_id {
            clauses.push("r.walk_id=?".into());
            values.push(Value::Integer(walk));
        }
        if !clauses.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&clauses.join(" AND "));
        }
        sql.push_str(" ORDER BY r.id DESC");
        if let Some(limit) = filter.limit {
            sql.push_str(" LIMIT ?");
            values.push(Value::Integer(
                i64::try_from(limit).context("limit is too large")?,
            ));
        }
        collect(
            &self.conn,
            &sql,
            rusqlite::params_from_iter(values),
            reading_row,
        )
    }
    fn reading_query(&self) -> Result<String> {
        let cols = columns(&self.conn, "readings")?;
        let has_walks =
            !columns(&self.conn, "walks")?.is_empty() && cols.iter().any(|c| c == "walk_id");
        let has_aps = !columns(&self.conn, "access_points")?.is_empty();
        let has_benchmarks = !columns(&self.conn, "benchmarks")?.is_empty();
        let mut query = READING_QUERY.to_owned();
        if !cols.iter().any(|c| c == "ssid_id") {
            query = query
                .replace("n.name AS ssid", "r.ssid AS ssid")
                .replace(" LEFT JOIN ssids n ON n.id=r.ssid_id", "")
                .replace("r.*", "r.*,NULL AS ssid_id");
        }
        if !has_walks {
            query = query.replace("w.name AS walk_name,w.started_at AS walk_started_at,w.ended_at AS walk_ended_at", "NULL AS walk_name,NULL AS walk_started_at,NULL AS walk_ended_at").replace(" LEFT JOIN walks w ON w.id=r.walk_id", "");
        }
        if !cols.iter().any(|c| c == "walk_id") {
            query = query.replace("r.*", "r.*,NULL AS walk_id");
        }
        if !has_aps {
            query = query
                .replace("a.name AS ap_name", "NULL AS ap_name")
                .replace(" LEFT JOIN access_points a ON a.bssid=r.bssid", "");
        }
        if !has_benchmarks {
            query = query.replace("r.rssi-b.rssi AS delta_rssi,r.snr-b.snr AS delta_snr,round(r.down_mbps-b.down_mbps,1) AS delta_down_mbps,round(r.up_mbps-b.up_mbps,1) AS delta_up_mbps", "NULL AS delta_rssi,NULL AS delta_snr,NULL AS delta_down_mbps,NULL AS delta_up_mbps").replace(" LEFT JOIN benchmarks b ON b.location_id=m.location_id", "");
        }
        for (column, _) in PATH_COLUMNS {
            if !cols.iter().any(|c| c == column) {
                query = query.replace("r.*", &format!("r.*,NULL AS {column}"));
            }
        }
        Ok(query)
    }
    pub fn set_access_point_name(&self, bssid: &str, name: &str) -> Result<AccessPoint> {
        let bssid = normalize_bssid(bssid)?;
        let name = name.trim();
        ensure!(!name.is_empty(), "access-point name must not be blank");
        self.conn.execute("INSERT INTO access_points(bssid,name) VALUES (?,?) ON CONFLICT(bssid) DO UPDATE SET name=excluded.name",params![bssid,name])?;
        Ok(AccessPoint {
            bssid,
            name: name.into(),
        })
    }
    pub fn get_access_point_name(&self, bssid: &str) -> Result<Option<String>> {
        Ok(self
            .conn
            .query_row(
                "SELECT name FROM access_points WHERE bssid=?",
                [normalize_bssid(bssid)?],
                |r| r.get(0),
            )
            .optional()?)
    }
    pub fn list_access_points(&self) -> Result<Vec<AccessPoint>> {
        collect(
            &self.conn,
            "SELECT bssid,name FROM access_points ORDER BY name,bssid",
            [],
            |r| {
                Ok(AccessPoint {
                    bssid: r.get(0)?,
                    name: r.get(1)?,
                })
            },
        )
    }
    pub fn set_benchmark(&self, location: i64, reading: &Reading) -> Result<()> {
        self.require_location(location)?;
        let ssid =
            self.validated_ssid(location, reading.ssid_id, reading.signal.ssid.as_deref())?;
        let ts = if reading.ts.is_empty() {
            timestamp()
        } else {
            reading.ts.clone()
        };
        let s = &reading.signal;
        let v = &reading.speed;
        self.conn.execute("INSERT INTO benchmarks(location_id,ts,ssid_id,bssid,rssi,noise,snr,channel,phy,tx_rate,ping_ms,down_mbps,up_mbps,server,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(location_id) DO UPDATE SET ts=excluded.ts,ssid_id=excluded.ssid_id,bssid=excluded.bssid,rssi=excluded.rssi,noise=excluded.noise,snr=excluded.snr,channel=excluded.channel,phy=excluded.phy,tx_rate=excluded.tx_rate,ping_ms=excluded.ping_ms,down_mbps=excluded.down_mbps,up_mbps=excluded.up_mbps,server=excluded.server,note=excluded.note",params![location,ts,ssid,s.bssid,s.rssi,s.noise,s.snr,s.channel,s.phy,s.tx_rate,v.ping_ms,v.down_mbps,v.up_mbps,v.server,reading.note])?;
        Ok(())
    }
    pub fn get_benchmark(&self, location: i64) -> Result<Option<Reading>> {
        Ok(self.conn.query_row("SELECT b.location_id,b.ts,b.ssid_id,n.name AS ssid,b.bssid,b.rssi,b.noise,b.snr,b.channel,b.phy,b.tx_rate,b.ping_ms,b.down_mbps,b.up_mbps,b.server,b.note,l.name AS location_name FROM benchmarks b LEFT JOIN ssids n ON n.id=b.ssid_id JOIN locations l ON l.id=b.location_id WHERE b.location_id=?",[location],|r|Ok(Reading {location_id:r.get("location_id")?,location:r.get("location_name")?,ts:r.get("ts")?,ssid_id:r.get("ssid_id")?,signal:signal_row(r)?,speed:speed_row(r)?,note:r.get("note")?,..Default::default()})).optional()?)
    }
    pub fn clear_benchmark(&self, location: i64) -> Result<()> {
        self.conn
            .execute("DELETE FROM benchmarks WHERE location_id=?", [location])?;
        Ok(())
    }
}

pub fn normalize_bssid(value: &str) -> Result<String> {
    let bssid = value.trim();
    let parts = bssid.split([':', '-']).collect::<Vec<_>>();
    ensure!(
        parts.len() == 6
            && parts
                .iter()
                .all(|p| p.len() == 2 && p.bytes().all(|b| b.is_ascii_hexdigit())),
        "invalid BSSID: {value:?}"
    );
    Ok(parts.join(":").to_ascii_lowercase())
}

pub fn format_benchmark_delta(current: &Reading, benchmark: Option<&Reading>) -> String {
    let Some(b) = benchmark else {
        return String::new();
    };
    let mut parts = Vec::new();
    for (name, a, b) in [
        ("rssi", current.signal.rssi, b.signal.rssi),
        ("snr", current.signal.snr, b.signal.snr),
    ] {
        if let (Some(a), Some(b)) = (a, b) {
            let difference = (a - b).round_ties_even();
            let difference = if difference == 0.0 { 0.0 } else { difference };
            parts.push(format!("{name} {difference:+.0}"));
        }
    }
    for (name, a, b) in [
        ("down", current.speed.down_mbps, b.speed.down_mbps),
        ("up", current.speed.up_mbps, b.speed.up_mbps),
    ] {
        if let (Some(a), Some(b)) = (a, b) {
            parts.push(format!("{name} {:+.1}", a - b));
        }
    }
    parts.join(", ")
}

fn columns(conn: &Connection, table: &str) -> Result<Vec<String>> {
    collect(conn, &format!("PRAGMA table_info({table})"), [], |r| {
        r.get(1)
    })
}
fn collect<T, P: rusqlite::Params, F: FnMut(&Row<'_>) -> rusqlite::Result<T>>(
    conn: &Connection,
    sql: &str,
    params: P,
    map: F,
) -> Result<Vec<T>> {
    Ok(conn
        .prepare(sql)?
        .query_map(params, map)?
        .collect::<rusqlite::Result<Vec<_>>>()?)
}
fn location_row(r: &Row<'_>) -> rusqlite::Result<Location> {
    Ok(Location {
        id: r.get(0)?,
        name: r.get(1)?,
    })
}
fn room_row(r: &Row<'_>) -> rusqlite::Result<Room> {
    Ok(Room {
        id: r.get(0)?,
        location_id: r.get(1)?,
        name: r.get(2)?,
        floor: r.get(3)?,
        outdoors: r.get(4)?,
    })
}
fn spot_row(r: &Row<'_>) -> rusqlite::Result<Spot> {
    Ok(Spot {
        id: r.get(0)?,
        room_id: r.get(1)?,
        name: r.get(2)?,
    })
}
fn ssid_row(r: &Row<'_>) -> rusqlite::Result<Ssid> {
    Ok(Ssid {
        id: r.get(0)?,
        location_id: r.get(1)?,
        name: r.get(2)?,
    })
}
fn walk_row(r: &Row<'_>) -> rusqlite::Result<Walk> {
    Ok(Walk {
        id: r.get(0)?,
        location_id: r.get(1)?,
        name: r.get(2)?,
        started_at: r.get(3)?,
        ended_at: r.get(4)?,
        ssid_id: r.get(5)?,
    })
}
fn signal_row(r: &Row<'_>) -> rusqlite::Result<Signal> {
    Ok(Signal {
        ssid: r.get("ssid")?,
        bssid: r.get("bssid")?,
        rssi: r.get("rssi")?,
        noise: r.get("noise")?,
        snr: r.get("snr")?,
        channel: r.get("channel")?,
        phy: r.get("phy")?,
        tx_rate: r.get("tx_rate")?,
        ..Default::default()
    })
}
fn speed_row(r: &Row<'_>) -> rusqlite::Result<Speed> {
    Ok(Speed {
        ping_ms: r.get("ping_ms")?,
        down_mbps: r.get("down_mbps")?,
        up_mbps: r.get("up_mbps")?,
        server: r.get("server")?,
    })
}
fn reading_row(r: &Row<'_>) -> rusqlite::Result<Reading> {
    Ok(Reading {
        id: r.get("id")?,
        ts: r.get("ts")?,
        spot_id: r.get("spot_id")?,
        ssid_id: r.get("ssid_id")?,
        location_id: r.get("location_id")?,
        location: r.get("location_name")?,
        room_id: r.get("room_id")?,
        room: r.get("room_name")?,
        floor: r.get("floor")?,
        outdoors: r.get("outdoors")?,
        spot: r.get("spot_name")?,
        signal: signal_row(r)?,
        speed: speed_row(r)?,
        path: PathFields {
            path_probe_count: r.get("path_probe_count")?,
            gateway_rtt_ms: r.get("gateway_rtt_ms")?,
            gateway_p95_ms: r.get("gateway_p95_ms")?,
            gateway_loss_pct: r.get("gateway_loss_pct")?,
            gateway_max_outage_ms: r.get("gateway_max_outage_ms")?,
            internet_rtt_ms: r.get("internet_rtt_ms")?,
            internet_p95_ms: r.get("internet_p95_ms")?,
            internet_loss_pct: r.get("internet_loss_pct")?,
            internet_max_outage_ms: r.get("internet_max_outage_ms")?,
        },
        note: r.get("note")?,
        ap_name: r.get("ap_name")?,
        walk_id: r.get("walk_id")?,
        walk_name: r.get("walk_name")?,
        walk_started_at: r.get("walk_started_at")?,
        walk_ended_at: r.get("walk_ended_at")?,
        delta_rssi: r.get("delta_rssi")?,
        delta_snr: r.get("delta_snr")?,
        delta_down_mbps: r.get("delta_down_mbps")?,
        delta_up_mbps: r.get("delta_up_mbps")?,
    })
}

const PATH_COLUMNS: &[(&str, &str)] = &[
    ("path_probe_count", "INTEGER"),
    ("gateway_rtt_ms", "REAL"),
    ("gateway_p95_ms", "REAL"),
    ("gateway_loss_pct", "REAL"),
    ("gateway_max_outage_ms", "INTEGER"),
    ("internet_rtt_ms", "REAL"),
    ("internet_p95_ms", "REAL"),
    ("internet_loss_pct", "REAL"),
    ("internet_max_outage_ms", "INTEGER"),
];
const READING_QUERY: &str = "SELECT r.*,n.name AS ssid,a.name AS ap_name,w.name AS walk_name,w.started_at AS walk_started_at,w.ended_at AS walk_ended_at,s.name AS spot_name,s.room_id,m.name AS room_name,m.location_id,m.floor,m.outdoors,l.name AS location_name,r.rssi-b.rssi AS delta_rssi,r.snr-b.snr AS delta_snr,round(r.down_mbps-b.down_mbps,1) AS delta_down_mbps,round(r.up_mbps-b.up_mbps,1) AS delta_up_mbps FROM readings r JOIN spots s ON s.id=r.spot_id JOIN rooms m ON m.id=s.room_id JOIN locations l ON l.id=m.location_id LEFT JOIN ssids n ON n.id=r.ssid_id LEFT JOIN access_points a ON a.bssid=r.bssid LEFT JOIN walks w ON w.id=r.walk_id LEFT JOIN benchmarks b ON b.location_id=m.location_id";

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS locations(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS ssids(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  UNIQUE(location_id, name)
);
CREATE TABLE IF NOT EXISTS access_points(
  bssid TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK(trim(name) <> '')
);
CREATE TABLE IF NOT EXISTS walks(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  ssid_id INTEGER REFERENCES ssids(id),
  name TEXT NOT NULL CHECK(trim(name) <> ''),
  started_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  name TEXT NOT NULL,
  floor INTEGER NOT NULL DEFAULT 0,
  outdoors INTEGER NOT NULL DEFAULT 0 CHECK(outdoors IN (0, 1)),
  UNIQUE(location_id, name, floor)
);
CREATE TABLE IF NOT EXISTS spots(
  id INTEGER PRIMARY KEY,
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  name TEXT NOT NULL,
  UNIQUE(room_id, name)
);
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  spot_id INTEGER NOT NULL REFERENCES spots(id),
  ssid_id INTEGER REFERENCES ssids(id), bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  path_probe_count INTEGER,
  gateway_rtt_ms REAL, gateway_p95_ms REAL, gateway_loss_pct REAL,
  gateway_max_outage_ms INTEGER,
  internet_rtt_ms REAL, internet_p95_ms REAL, internet_loss_pct REAL,
  internet_max_outage_ms INTEGER,
  server TEXT, note TEXT,
  walk_id INTEGER REFERENCES walks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_rooms_location ON rooms(location_id);
CREATE INDEX IF NOT EXISTS idx_ssids_location_name ON ssids(location_id, name);
CREATE INDEX IF NOT EXISTS idx_spots_room ON spots(room_id);
CREATE INDEX IF NOT EXISTS idx_readings_spot ON readings(spot_id);
CREATE INDEX IF NOT EXISTS idx_readings_ssid ON readings(ssid_id);
CREATE TABLE IF NOT EXISTS benchmarks(
  location_id INTEGER PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  ssid_id INTEGER REFERENCES ssids(id),
  bssid TEXT,
  rssi INTEGER, noise INTEGER, snr INTEGER,
  channel TEXT, phy TEXT, tx_rate TEXT,
  ping_ms REAL, down_mbps REAL, up_mbps REAL,
  server TEXT, note TEXT
);
"#;
