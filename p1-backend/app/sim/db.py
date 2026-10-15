"""The two plant databases (SQLite, WAL). OT and IT are separate stores, like in a real plant:

- OT  (ot.db): equipment hierarchy as the control system sees it, tags, historian, machine states, alarms, and the
  simulator's ground truth (asset_conditions: the real fault, never visible to agents or UI).
- IT  (it.db): ERP (parts, stock, suppliers, transfers), MES (lines, orders, schedule, production log), CMMS
  (equipment master, BOM, work orders and their updates), workforce (technicians, skills, shifts), QMS (defects,
  inspections), identity (users, sessions), PlantGuard itself (incidents, events, options, approvals, verifications,
  feedback, audit) and the document catalogue behind the FAISS index.

Every table in a database is linked to the others by foreign keys (enforced). The two databases share the machine
id (`asset_id`) and plant/line ids, which is the integration key between OT and IT.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

OT_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sites (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lines (
  site_id TEXT NOT NULL REFERENCES sites(id), id TEXT NOT NULL, name TEXT NOT NULL,
  PRIMARY KEY (site_id, id));
CREATE TABLE IF NOT EXISTS stations (
  site_id TEXT NOT NULL, line_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, seq INTEGER NOT NULL,
  PRIMARY KEY (site_id, id), FOREIGN KEY (site_id, line_id) REFERENCES lines(site_id, id));
CREATE TABLE IF NOT EXISTS equipment (
  asset_id TEXT PRIMARY KEY, site_id TEXT NOT NULL, line_id TEXT NOT NULL, station_id TEXT NOT NULL,
  name TEXT NOT NULL, type TEXT NOT NULL, controller TEXT NOT NULL, pos_x REAL NOT NULL, pos_y REAL NOT NULL,
  FOREIGN KEY (site_id, station_id) REFERENCES stations(site_id, id));
CREATE TABLE IF NOT EXISTS tags (
  tag TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES equipment(asset_id), signal TEXT NOT NULL,
  unit TEXT NOT NULL, protocol TEXT NOT NULL, range_min REAL NOT NULL, range_max REAL NOT NULL,
  UNIQUE (asset_id, signal));
CREATE TABLE IF NOT EXISTS historian (
  asset_id TEXT NOT NULL REFERENCES equipment(asset_id), ts TEXT NOT NULL, state TEXT NOT NULL,
  speed_pct INTEGER NOT NULL, vibration_rms REAL, vibration_peak REAL, vibration_kurtosis REAL, bearing_temp REAL,
  motor_temp REAL, spindle_rpm REAL, motor_current REAL, power_kw REAL, quality TEXT NOT NULL DEFAULT 'good',
  PRIMARY KEY (asset_id, ts));
CREATE TABLE IF NOT EXISTS machine_state (
  asset_id TEXT PRIMARY KEY REFERENCES equipment(asset_id), state TEXT NOT NULL, reason TEXT, since TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS machine_state_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL REFERENCES equipment(asset_id), state TEXT NOT NULL,
  reason TEXT, start_ts TEXT NOT NULL, end_ts TEXT);
CREATE TABLE IF NOT EXISTS alarm_defs (
  code TEXT PRIMARY KEY, signal TEXT NOT NULL, severity TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alarms (
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES equipment(asset_id), code TEXT NOT NULL REFERENCES alarm_defs(code),
  severity TEXT NOT NULL, message TEXT NOT NULL, value REAL, raised_at TEXT NOT NULL, cleared_at TEXT,
  acknowledged INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS alarms_active ON alarms(asset_id, cleared_at);
CREATE TABLE IF NOT EXISTS asset_conditions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL REFERENCES equipment(asset_id),
  fault_mode TEXT NOT NULL, variant TEXT NOT NULL, root_cause TEXT NOT NULL, fix_part TEXT NOT NULL,
  progress REAL NOT NULL, breakdown_h REAL NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, end_reason TEXT);
CREATE INDEX IF NOT EXISTS conditions_open ON asset_conditions(asset_id, ended_at);
"""

IT_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- ---------------------------------------------------------------- master data
CREATE TABLE IF NOT EXISTS plants (id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS storage_locations (
  id TEXT PRIMARY KEY, plant_id TEXT NOT NULL REFERENCES plants(id), name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lines (
  plant_id TEXT NOT NULL REFERENCES plants(id), id TEXT NOT NULL, name TEXT NOT NULL, product TEXT NOT NULL,
  rate_units_per_h REAL NOT NULL, PRIMARY KEY (plant_id, id));
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, asset_type TEXT, equipment_model TEXT,
  asset_id TEXT, source TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS doc_chunks (
  id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id), section TEXT NOT NULL,
  heading TEXT NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS equipment (
  asset_id TEXT PRIMARY KEY, plant_id TEXT NOT NULL, line_id TEXT NOT NULL, station_id TEXT NOT NULL,
  station_name TEXT NOT NULL, station_seq INTEGER NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL,
  model TEXT NOT NULL, manufacturer TEXT NOT NULL, manual_doc_id TEXT REFERENCES documents(id),
  pos_x REAL NOT NULL, pos_y REAL NOT NULL,
  FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
CREATE TABLE IF NOT EXISTS suppliers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, lead_time_days INTEGER NOT NULL, express_available INTEGER NOT NULL,
  express_lead_time_days INTEGER);
CREATE TABLE IF NOT EXISTS parts (
  sku TEXT PRIMARY KEY, description TEXT NOT NULL, unit_cost REAL NOT NULL,
  supplier_id TEXT NOT NULL REFERENCES suppliers(id), repair_skill TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock (
  sku TEXT NOT NULL REFERENCES parts(sku), plant_id TEXT NOT NULL REFERENCES plants(id),
  location_id TEXT NOT NULL REFERENCES storage_locations(id), quantity INTEGER NOT NULL CHECK (quantity >= 0),
  PRIMARY KEY (sku, plant_id));
CREATE TABLE IF NOT EXISTS bom_items (
  asset_id TEXT NOT NULL REFERENCES equipment(asset_id), sku TEXT NOT NULL REFERENCES parts(sku),
  quantity INTEGER NOT NULL, pos INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (asset_id, sku));
-- ---------------------------------------------------------------- workforce + identity
CREATE TABLE IF NOT EXISTS technicians (
  id TEXT PRIMARY KEY, plant_id TEXT NOT NULL REFERENCES plants(id), name TEXT NOT NULL, crew INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS technician_skills (
  technician_id TEXT NOT NULL REFERENCES technicians(id), skill TEXT NOT NULL, PRIMARY KEY (technician_id, skill));
CREATE TABLE IF NOT EXISTS technician_certifications (
  technician_id TEXT NOT NULL REFERENCES technicians(id), certification TEXT NOT NULL,
  PRIMARY KEY (technician_id, certification));
CREATE TABLE IF NOT EXISTS shifts (
  id TEXT PRIMARY KEY, plant_id TEXT NOT NULL REFERENCES plants(id), name TEXT NOT NULL, start_ts TEXT NOT NULL,
  end_ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shift_assignments (
  shift_id TEXT NOT NULL REFERENCES shifts(id), technician_id TEXT NOT NULL REFERENCES technicians(id),
  PRIMARY KEY (shift_id, technician_id));
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL,
  password_hash TEXT NOT NULL, technician_id TEXT REFERENCES technicians(id), active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS user_plants (
  user_id TEXT NOT NULL REFERENCES users(id), plant_id TEXT NOT NULL REFERENCES plants(id),
  PRIMARY KEY (user_id, plant_id));
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT, user_agent TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id, revoked_at);
-- ---------------------------------------------------------------- MES
CREATE TABLE IF NOT EXISTS production_orders (
  id TEXT PRIMARY KEY, plant_id TEXT NOT NULL, line_id TEXT NOT NULL, product TEXT NOT NULL, quantity INTEGER NOT NULL,
  completed INTEGER NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL, seq INTEGER NOT NULL,
  started_at TEXT, completed_at TEXT, FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
CREATE TABLE IF NOT EXISTS production_log (
  plant_id TEXT NOT NULL, line_id TEXT NOT NULL, hour_ts TEXT NOT NULL, order_id TEXT REFERENCES production_orders(id),
  planned_min REAL NOT NULL, run_min REAL NOT NULL, speed_sum REAL NOT NULL, units REAL NOT NULL,
  defects INTEGER NOT NULL, PRIMARY KEY (plant_id, line_id, hour_ts),
  FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
CREATE TABLE IF NOT EXISTS defects (
  id TEXT PRIMARY KEY, plant_id TEXT NOT NULL, line_id TEXT NOT NULL, asset_id TEXT REFERENCES equipment(asset_id),
  order_id TEXT REFERENCES production_orders(id), defect_type TEXT NOT NULL, measured REAL NOT NULL,
  spec_min REAL NOT NULL, spec_max REAL NOT NULL, ts TEXT NOT NULL,
  FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
CREATE TABLE IF NOT EXISTS inspections (
  id TEXT PRIMARY KEY, plant_id TEXT NOT NULL, line_id TEXT NOT NULL, lot_id TEXT NOT NULL,
  order_id TEXT REFERENCES production_orders(id), passed INTEGER NOT NULL, ts TEXT NOT NULL,
  FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
-- ---------------------------------------------------------------- PlantGuard incidents (+ links into CMMS/ERP/MES)
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES equipment(asset_id), plant_id TEXT NOT NULL,
  line_id TEXT NOT NULL, status TEXT NOT NULL, trigger TEXT NOT NULL, parent_id TEXT REFERENCES incidents(id),
  round INTEGER NOT NULL DEFAULT 1, assigned_technician_id TEXT REFERENCES technicians(id),
  work_order_id TEXT, approved_by TEXT REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  resolved_at TEXT, payload TEXT NOT NULL, run_state TEXT NOT NULL DEFAULT 'pending',
  version INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
CREATE INDEX IF NOT EXISTS incidents_status ON incidents(status);
CREATE TABLE IF NOT EXISTS incident_events (
  incident_id TEXT NOT NULL REFERENCES incidents(id), seq INTEGER NOT NULL, round INTEGER NOT NULL,
  ts TEXT NOT NULL, agent TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (incident_id, seq));
CREATE TABLE IF NOT EXISTS incident_options (
  incident_id TEXT NOT NULL REFERENCES incidents(id), round INTEGER NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY (incident_id, round));
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), round INTEGER NOT NULL,
  option_id TEXT NOT NULL, decision TEXT NOT NULL, approver_id TEXT NOT NULL REFERENCES users(id),
  approved_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_orders (
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES equipment(asset_id), type TEXT NOT NULL, title TEXT NOT NULL,
  root_cause TEXT, findings TEXT, technician_id TEXT REFERENCES technicians(id), planned_start TEXT,
  duration_hours REAL, status TEXT NOT NULL, incident_id TEXT REFERENCES incidents(id), approval_id TEXT REFERENCES approvals(id),
  created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS work_order_parts (
  work_order_id TEXT NOT NULL REFERENCES work_orders(id), sku TEXT NOT NULL REFERENCES parts(sku),
  quantity INTEGER NOT NULL, PRIMARY KEY (work_order_id, sku));
CREATE TABLE IF NOT EXISTS work_order_updates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, work_order_id TEXT NOT NULL REFERENCES work_orders(id), ts TEXT NOT NULL,
  author_id TEXT REFERENCES users(id), kind TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_transfers (
  id TEXT PRIMARY KEY, sku TEXT NOT NULL REFERENCES parts(sku), from_plant TEXT NOT NULL REFERENCES plants(id),
  to_plant TEXT NOT NULL REFERENCES plants(id), quantity INTEGER NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, departs_at TEXT NOT NULL, eta TEXT NOT NULL, delivered_at TEXT, cost REAL NOT NULL,
  incident_id TEXT REFERENCES incidents(id), approval_id TEXT REFERENCES approvals(id));
CREATE TABLE IF NOT EXISTS stock_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL REFERENCES parts(sku), plant_id TEXT NOT NULL REFERENCES plants(id),
  qty_delta INTEGER NOT NULL, reason TEXT NOT NULL, transfer_id TEXT REFERENCES stock_transfers(id),
  work_order_id TEXT REFERENCES work_orders(id), ts TEXT NOT NULL,
  purchase_order_id TEXT REFERENCES purchase_orders(id));
CREATE TABLE IF NOT EXISTS line_speed_plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plant_id TEXT NOT NULL, line_id TEXT NOT NULL, speed_pct INTEGER NOT NULL,
  start_ts TEXT NOT NULL, end_ts TEXT, incident_id TEXT REFERENCES incidents(id), approval_id TEXT REFERENCES approvals(id),
  created_at TEXT NOT NULL, FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
CREATE TABLE IF NOT EXISTS schedule_slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plant_id TEXT NOT NULL, line_id TEXT NOT NULL, start_ts TEXT NOT NULL,
  end_ts TEXT NOT NULL, kind TEXT NOT NULL, order_id TEXT REFERENCES production_orders(id),
  planned_speed_pct INTEGER NOT NULL, work_order_id TEXT REFERENCES work_orders(id),
  FOREIGN KEY (plant_id, line_id) REFERENCES lines(plant_id, id));
CREATE TABLE IF NOT EXISTS repair_verifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL REFERENCES incidents(id),
  work_order_id TEXT NOT NULL REFERENCES work_orders(id), started_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  result TEXT NOT NULL, max_risk REAL, details TEXT);
CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), technician_id TEXT REFERENCES technicians(id),
  author_id TEXT REFERENCES users(id), payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
  seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  resource TEXT NOT NULL, details TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS id_sequences (name TEXT PRIMARY KEY, next INTEGER NOT NULL);
-- ---------------------------------------------------------------- v1.2: purchasing + options chat
CREATE TABLE IF NOT EXISTS purchase_orders (
  id TEXT PRIMARY KEY, sku TEXT NOT NULL REFERENCES parts(sku), supplier_id TEXT NOT NULL REFERENCES suppliers(id),
  to_plant TEXT NOT NULL REFERENCES plants(id), quantity INTEGER NOT NULL, express INTEGER NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, eta TEXT NOT NULL, delivered_at TEXT, cost REAL NOT NULL,
  incident_id TEXT REFERENCES incidents(id), approval_id TEXT REFERENCES approvals(id));
CREATE TABLE IF NOT EXISTS option_chat (
  incident_id TEXT NOT NULL REFERENCES incidents(id), round INTEGER NOT NULL, seq INTEGER NOT NULL,
  role TEXT NOT NULL, author_id TEXT REFERENCES users(id), text TEXT NOT NULL, option_id TEXT, ts TEXT NOT NULL,
  PRIMARY KEY (incident_id, round, seq));
"""


class Database:
    """One SQLite file shared by every thread of the backend (the event loop and tool worker threads).
    Writes are serialised with a lock; foreign keys are enforced on every connection. Single process only:
    production uses PgDatabase (sim/pgdb.py) with the same interface."""

    dialect = "sqlite"

    def __init__(self, path: Path, schema: str):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(schema)
            self._migrate()

    def _migrate(self) -> None:
        """Columns added after a plant was first created (CREATE TABLE IF NOT EXISTS does not add them)."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(stock_movements)").fetchall()}
        if cols and "purchase_order_id" not in cols:
            self._conn.execute("ALTER TABLE stock_movements ADD COLUMN purchase_order_id TEXT REFERENCES purchase_orders(id)")
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(incidents)").fetchall()}
        if cols and "version" not in cols:
            self._conn.execute("ALTER TABLE incidents ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(bom_items)").fetchall()}
        if cols and "pos" not in cols:  # BOM order used to be the insertion order
            self._conn.execute("ALTER TABLE bom_items ADD COLUMN pos INTEGER NOT NULL DEFAULT 0")
            self._conn.execute("UPDATE bom_items SET pos=rowid")

    # ---- queries ----
    def q(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def val(self, sql: str, args: tuple | list = (), default: Any = None) -> Any:
        row = self.one(sql, args)
        return row[0] if row is not None and row[0] is not None else default

    def x(self, sql: str, args: tuple | list = ()) -> int:
        """Execute one statement; returns lastrowid."""
        with self._lock:
            return self._conn.execute(sql, args).lastrowid

    def insert_id(self, sql: str, args: tuple | list = ()) -> int:
        """INSERT and return the generated `id` column."""
        with self._lock:
            return self._conn.execute(sql, args).lastrowid

    def xc(self, sql: str, args: tuple | list = ()) -> int:
        """Execute one statement; returns the number of rows changed."""
        with self._lock:
            return self._conn.execute(sql, args).rowcount

    def many(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    def advisory_lock(self, conn, key: str) -> None:
        """Single process: the transaction lock already serialises writers."""
        return None

    def ping(self) -> bool:
        return self.val("SELECT 1") == 1

    @contextmanager
    def session_lock(self, key: str, timeout_s: float = 600):
        """One process owns a SQLite file: nothing to coordinate."""
        yield

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A transaction: everything inside commits together or not at all."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # ---- key/value + id sequences ----
    def meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.x("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))

    def next_id(self, name: str, start: int) -> int:
        """Persistent counter (survives restarts) used for INC-, WO-, STO-, APR- ... ids."""
        with self._lock:
            row = self._conn.execute("SELECT next FROM id_sequences WHERE name=?", (name,)).fetchone()
            n = row["next"] if row else start
            self._conn.execute("INSERT INTO id_sequences(name, next) VALUES(?, ?) ON CONFLICT(name) DO UPDATE "
                               "SET next=excluded.next", (name, n + 1))
            return n

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def database_urls() -> tuple[str | None, str | None]:
    """PostgreSQL when both are known, else SQLite files. Either full URLs:
      PLANTGUARD_OT_DATABASE_URL / PLANTGUARD_IT_DATABASE_URL = postgresql://user:pass@host:5432/db
    or the parts (Cloud Run: the password comes from Secret Manager as its own variable):
      DB_HOST, DB_PORT (5432), DB_USER, DB_PASSWORD, DB_NAME_OT, DB_NAME_IT, DB_SSLMODE (require)"""
    ot, it = os.getenv("PLANTGUARD_OT_DATABASE_URL") or None, os.getenv("PLANTGUARD_IT_DATABASE_URL") or None
    host = os.getenv("DB_HOST")
    if not (ot and it) and host:
        from urllib.parse import quote
        user, pw = quote(os.getenv("DB_USER", "plantguard"), safe=""), quote(os.getenv("DB_PASSWORD", ""), safe="")
        base = f"postgresql://{user}{':' + pw if pw else ''}@{host}:{os.getenv('DB_PORT', '5432')}"
        ssl = os.getenv("DB_SSLMODE", "require")
        ot = ot or f"{base}/{os.getenv('DB_NAME_OT', 'plantguard_ot')}?sslmode={ssl}&application_name=plantguard"
        it = it or f"{base}/{os.getenv('DB_NAME_IT', 'plantguard_it')}?sslmode={ssl}&application_name=plantguard"
    return ot, it


def open_databases(data_dir: Path, wipe_first: bool = False):
    """PostgreSQL when both database URLs are set (production, several processes), else the SQLite files."""
    ot_url, it_url = database_urls()
    if ot_url and it_url:
        from app.sim.pgdb import PgDatabase
        pool = int(os.getenv("PLANTGUARD_DB_POOL_SIZE", "10"))
        if wipe_first:
            for url, schema, name in ((ot_url, OT_SCHEMA, "ot"), (it_url, IT_SCHEMA, "it")):
                tmp = PgDatabase(url, "", name, max_size=1)
                tmp.wipe()
                tmp.close()
        return (PgDatabase(ot_url, OT_SCHEMA, "ot", max_size=pool,
                           migrations=["CREATE INDEX IF NOT EXISTS historian_ts ON historian(ts)"]),
                PgDatabase(it_url, IT_SCHEMA, "it", max_size=pool,
                           migrations=["ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS purchase_order_id TEXT "
                                       "REFERENCES purchase_orders(id)",
                                       "ALTER TABLE bom_items ADD COLUMN IF NOT EXISTS pos INTEGER NOT NULL DEFAULT 0",
                                       "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 0",
                                       "CREATE INDEX IF NOT EXISTS incidents_updated ON incidents(status, updated_at)",
                                       "CREATE INDEX IF NOT EXISTS incidents_asset ON incidents(asset_id, status)",
                                       "CREATE INDEX IF NOT EXISTS events_round ON incident_events(incident_id, round)"]))
    return Database(data_dir / "ot.db", OT_SCHEMA), Database(data_dir / "it.db", IT_SCHEMA)
