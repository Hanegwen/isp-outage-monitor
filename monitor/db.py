import os
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts          INTEGER NOT NULL,   -- unix seconds
    gateway_ok  INTEGER NOT NULL,
    modem_ok    INTEGER NOT NULL,
    internet_ok INTEGER NOT NULL,
    dns_ok      INTEGER NOT NULL,
    status      TEXT    NOT NULL    -- ok | isp_down | dns_down | lan_down
);
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS speedtests (
    ts            INTEGER NOT NULL,
    ok            INTEGER NOT NULL,
    download_mbps REAL,
    upload_mbps   REAL,
    ping_ms       REAL,
    server        TEXT,
    result_url    TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS speedtests_ts ON speedtests(ts);

CREATE TABLE IF NOT EXISTS usage (      -- hourly router Traffic Meter readings, MB
    ts            INTEGER NOT NULL,
    month         TEXT    NOT NULL,     -- YYYY-MM
    today_mb      REAL, yesterday_mb REAL,
    month_mb      REAL, month_up_mb REAL, month_down_mb REAL,
    last_month_mb REAL
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);

CREATE TABLE IF NOT EXISTS usage_alerts (  -- alerts already emailed, e.g. cap90:2026-10
    key TEXT PRIMARY KEY,
    ts  INTEGER NOT NULL
);
"""


def connect(path=None):
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
