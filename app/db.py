"""SQLite storage: users, sessions, watchlists, alarms, alarm events, custom calendar events."""
import sqlite3
import threading
from pathlib import Path

from .config import settings

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  email TEXT DEFAULT '',
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'analyst',
  active INTEGER NOT NULL DEFAULT 1,
  must_change INTEGER NOT NULL DEFAULT 1,
  failed INTEGER NOT NULL DEFAULT 0,
  locked_until REAL NOT NULL DEFAULT 0,
  created REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
  user_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  pos INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, symbol)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  contract TEXT,
  metric TEXT NOT NULL,
  op TEXT NOT NULL,
  threshold REAL NOT NULL,
  note TEXT DEFAULT '',
  channels TEXT NOT NULL DEFAULT '["app"]',
  repeat INTEGER NOT NULL DEFAULT 0,
  cooldown_min INTEGER NOT NULL DEFAULT 30,
  status TEXT NOT NULL DEFAULT 'active',
  last_value REAL,
  last_checked REAL,
  cooldown_until REAL NOT NULL DEFAULT 0,
  triggered_at REAL,
  last_error TEXT,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  value REAL,
  message TEXT NOT NULL,
  seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS calendar_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  time TEXT DEFAULT '',
  title TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'custom',
  created_by INTEGER
);
CREATE TABLE IF NOT EXISTS paper_accounts (
  user_id INTEGER PRIMARY KEY,
  cash REAL NOT NULL,
  starting REAL NOT NULL,
  realized REAL NOT NULL DEFAULT 0,
  fees REAL NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  pos_key TEXT NOT NULL,
  inst TEXT NOT NULL,
  qty REAL NOT NULL,
  avg_price REAL NOT NULL,
  last_fx REAL NOT NULL DEFAULT 1,
  opened REAL NOT NULL,
  UNIQUE (user_id, pos_key)
);
CREATE TABLE IF NOT EXISTS paper_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  pos_key TEXT NOT NULL,
  symbol TEXT NOT NULL,
  descr TEXT NOT NULL,
  inst TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  type TEXT NOT NULL,
  limit_price REAL,
  stop_price REAL,
  status TEXT NOT NULL,
  fill_price REAL,
  fill_fx REAL,
  fee REAL NOT NULL DEFAULT 0,
  realized REAL NOT NULL DEFAULT 0,
  note TEXT DEFAULT '',
  reason TEXT DEFAULT '',
  created REAL NOT NULL,
  filled_at REAL
);
CREATE TABLE IF NOT EXISTS paper_equity (
  user_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  equity REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_paper_orders_user ON paper_orders(user_id, id);
CREATE INDEX IF NOT EXISTS ix_paper_equity_user ON paper_equity(user_id, ts);
CREATE INDEX IF NOT EXISTS ix_alerts_user ON alerts(user_id);
CREATE INDEX IF NOT EXISTS ix_events_user ON alert_events(user_id, id);
"""


def connect():
    global _conn
    with _lock:
        if _conn is None:
            Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(settings.db_path, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
        return _conn


def reset():
    """Close the connection (used by tests when the DB path changes)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def q(sql, params=()):
    with _lock:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def x(sql, params=()):
    """Run a write statement; returns the number of rows changed."""
    with _lock:
        return connect().execute(sql, params).rowcount


def ins(sql, params=()):
    """Run an INSERT; returns the new row id."""
    with _lock:
        return connect().execute(sql, params).lastrowid
